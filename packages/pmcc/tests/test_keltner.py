"""The keltner substrate and gate: EMA/ATR math, the cold-start refusal, and the bar mirror."""

import pytest

from cherrypick.pmcc import db, keltner


def _bars(closes, *, spread=1.0):
    """Completed bars with a fixed high-low spread around each close, prev_close chained."""
    bars = []
    prev = None
    for i, c in enumerate(closes):
        bars.append(
            {
                "symbol": "TNA",
                "trade_date": f"2026-07-{i + 1:02d}",
                "day_open": c,
                "day_high": c + spread / 2,
                "day_low": c - spread / 2,
                "day_close": c,
                "prev_day_close": prev,
            }
        )
        prev = c
    return bars


def test_channel_flat_series():
    # A flat series: EMA == the price, ATR == the fixed daily range.
    bars = _bars([70.0] * 25, spread=1.0)
    chan = keltner.channel(bars)
    assert chan is not None
    assert chan["mid"] == pytest.approx(70.0)
    assert chan["atr"] == pytest.approx(1.0)
    assert chan["upper"] == pytest.approx(71.5)
    assert chan["lower"] == pytest.approx(68.5)
    assert chan["days"] == 25


def test_channel_ema_hand_computed():
    closes = [10.0] * 20 + [12.0]
    bars = _bars(closes, spread=0.5)
    chan = keltner.channel(bars)
    # Seed SMA(first 20) = 10; one EMA step with k = 2/21 on 12.0.
    k = 2.0 / 21
    assert chan["mid"] == pytest.approx(round(10.0 + (12.0 - 10.0) * k, 4))


def test_channel_refuses_short_history():
    assert keltner.channel(_bars([70.0] * 15)) is None


def test_entry_ok_full_pass_and_each_refusal():
    chan = {"mid": 70.0, "atr": 2.0, "upper": 73.0, "lower": 67.0, "days": 25}
    params = {"keltner_mid_band_atr": 0.5, "keltner_bounce_atr": 0.25}
    # Pass: spot 70.5 (0.25 ATR above mid), above prev close 70.0, bounced 0.6 off low 69.9.
    verdict = keltner.entry_ok(70.5, chan, prev_close=70.0, day_low=69.9, params=params)
    assert verdict["ok"], verdict
    assert verdict["measures"]["keltner_distance_atr"] == pytest.approx(0.25)
    # Above the band.
    assert (
        keltner.entry_ok(71.5, chan, prev_close=70.0, day_low=69.9, params=params)["reason"]
        == "keltner_above_band"
    )
    # Below the band.
    assert (
        keltner.entry_ok(68.5, chan, prev_close=70.0, day_low=68.0, params=params)["reason"]
        == "keltner_below_band"
    )
    # Below prior close.
    assert (
        keltner.entry_ok(70.5, chan, prev_close=70.8, day_low=69.9, params=params)["reason"]
        == "keltner_below_prev_close"
    )
    # No bounce off the low.
    assert (
        keltner.entry_ok(70.5, chan, prev_close=70.0, day_low=70.2, params=params)["reason"]
        == "keltner_no_bounce"
    )
    # Missing session inputs are their own refusals, never guesses.
    assert (
        keltner.entry_ok(70.5, chan, prev_close=None, day_low=69.9, params=params)["reason"]
        == "keltner_no_prev_close"
    )
    assert (
        keltner.entry_ok(70.5, chan, prev_close=70.0, day_low=None, params=params)["reason"]
        == "keltner_no_day_low"
    )
    # Cold start.
    assert keltner.entry_ok(70.5, None, prev_close=70.0, day_low=69.9, params=params)["reason"] == (
        "insufficient_bar_history"
    )


def test_upsert_daily_bars_mirrors_and_accumulates(cache, tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    cache.summary("TNA", "2026-08-13", o=70.0, h=71.0, low=69.5, c=70.5, prev=69.8)
    cache.summary("TNA", "2026-08-14", o=70.5, h=71.5, low=70.0, c=71.0, prev=70.5)
    assert keltner.upsert_daily_bars(conn, cache.path, ["TNA"]) == 2
    bars = keltner.completed_bars(conn, "TNA", "2026-08-15")
    assert [b["trade_date"] for b in bars] == ["2026-08-13", "2026-08-14"]
    # Re-mirror updates in place; a row the cache later drops SURVIVES here (the retention hedge).
    cache.conn.execute("DELETE FROM stream_summary WHERE trade_date = '2026-08-13'")
    cache.conn.commit()
    keltner.upsert_daily_bars(conn, cache.path, ["TNA"])
    assert len(keltner.completed_bars(conn, "TNA", "2026-08-15")) == 2
