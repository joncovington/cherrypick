"""The daily-bar regime classifier: trend x volatility over bars the streamer already backfills.

Every assertion here was confirmed to fail before the module existed, or against the specific
defect it names. The two that matter most are the two-route close read (a live-written SPX series
carries its closes only on the NEXT row's prev_day_close, and a single-route reader sees no bars at
all) and the no-look-ahead rule (a session's label must not move when tomorrow's bar arrives).
"""

from __future__ import annotations

import datetime as _dt
import time

import pytest

from cherrypick.core import marketregime as mr
from cherrypick.core import streamcache


def _bars(prices, *, start=None, span=1.0):
    """Synthetic bars on consecutive weekdays, high/low straddling the close."""
    import datetime as dt

    out = []
    day = start or dt.date(2026, 1, 5)
    for close in prices:
        while day.weekday() >= 5:
            day += dt.timedelta(days=1)
        out.append(
            {
                "session": day.isoformat(),
                "open": close,
                "high": close + span,
                "low": close - span,
                "close": close,
            }
        )
        day += dt.timedelta(days=1)
    return out


def test_true_range_has_no_value_for_the_first_bar():
    """A range measured against a close we do not have is a different measure wearing the name."""
    trs = mr.true_ranges(_bars([100.0, 101.0, 102.0]))
    assert trs[0] is None
    assert trs[1] == pytest.approx(2.0)


def test_a_rising_series_above_a_rising_average_is_an_uptrend():
    got = mr.classify(_bars([100.0 + i for i in range(60)]), "session")
    assert got["status"] == "measured"
    assert got["trend"] == "uptrend"
    assert got["quadrant"].startswith("uptrend/")


def test_the_mirror_series_is_a_downtrend():
    got = mr.classify(_bars([200.0 - i for i in range(60)]), "session")
    assert got["trend"] == "downtrend"


def test_an_oscillating_series_is_a_range():
    prices = [100.0 + (5.0 if i % 2 else 0.0) for i in range(60)]
    assert mr.classify(_bars(prices), "session")["trend"] == "range"


def test_volatility_is_where_this_stretch_sits_in_its_own_history():
    """The same trend at a wider range reads high-vol. Shown to fail with a fixed ATR threshold,
    which would call a quiet symbol calm and a loud one stressed forever."""
    calm = mr.classify(_bars([100.0 + i for i in range(60)], span=0.5), "session")
    assert calm["vol"] == "low"
    widening = _bars([100.0 + i for i in range(40)], span=0.5)
    widening += [
        {**bar, "high": bar["close"] + 8.0, "low": bar["close"] - 8.0}
        for bar in _bars([140.0 + i for i in range(20)], start=_dt.date(2026, 3, 2))
    ]
    assert mr.classify(widening, "session")["vol"] == "high"


def test_too_few_bars_refuses_with_a_reason_rather_than_labelling():
    got = mr.classify(_bars([100.0, 101.0, 102.0]), "swing")
    assert got["status"] == "unmeasured" and "needs" in got["reason"]
    assert "trend" not in got


def test_a_session_label_does_not_move_when_tomorrows_bar_arrives():
    """The no-look-ahead rule. Shown to fail with as_of=True, which grades a session against a
    close nobody had while it was being traded."""
    bars = _bars([100.0 + i for i in range(60)])
    upto = mr.series(bars[:-1], "session")
    full = mr.series(bars, "session")
    by_session = {row["session"]: row["quadrant"] for row in full}
    assert all(by_session[row["session"]] == row["quadrant"] for row in upto)
    assert full[-1]["session"] == bars[-1]["session"]
    post_hoc = mr.series(bars, "session", as_of=True)
    assert len(post_hoc) >= len(full)


def test_the_distribution_counts_quadrant_shares():
    labels = [{"quadrant": "uptrend/low"}] * 3 + [{"quadrant": "range/high"}]
    got = mr.distribution(labels)
    assert got["sessions"] == 4
    assert got["quadrants"]["uptrend/low"] == {"sessions": 3, "share": 0.75}


# --------------------------------------------------------------------------- against the cache
def _cache(tmp_path, rows):
    conn = streamcache.connect(tmp_path / "cache.db")
    for symbol, day, o, h, lo, c, prev in rows:
        conn.execute(
            "INSERT INTO stream_summary (symbol, trade_date, day_open, day_high, day_low, "
            "day_close, prev_day_close, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (symbol, day, o, h, lo, c, prev, time.time()),
        )
    conn.commit()
    return conn


def _live_shape(days: int) -> list[tuple]:
    """The live SPX shape: open/high/low every session, day_close NEVER, each close arriving on
    the NEXT row's prev_day_close."""
    import datetime as dt

    rows, day, close = [], dt.date(2026, 6, 1), 100.0
    prev = None
    for _ in range(days):
        while day.weekday() >= 5:
            day += dt.timedelta(days=1)
        rows.append(("SPX", day.isoformat(), close, close + 1.0, close - 1.0, None, prev))
        prev = close
        close += 1.0
        day += dt.timedelta(days=1)
    return rows


def test_build_reads_a_series_the_live_producer_never_wrote_a_close_for(tmp_path):
    """The consumer-level guard for the SPX close gap: every day_close is NULL, and the classifier
    still labels the series because the close arrives on the next row. A day_close-only read --
    which is what curve's regime_history does -- would find no bars at all."""
    conn = _cache(tmp_path, _live_shape(80))
    doc = mr.build(conn, "SPX", through="2026-09-30", horizon="session", generated_at="t")
    assert doc["coverage"]["bars"] > 60
    assert doc["latest"]["status"] == "measured"
    assert doc["distribution"]["sessions"] > 0
    assert doc["series"], "a live-written series must still produce labels"


def test_build_reports_no_bars_as_unmeasured_not_as_a_calm_market(tmp_path):
    conn = _cache(tmp_path, [])
    doc = mr.build(conn, "SPX", through="2026-09-30", generated_at="t")
    assert doc["latest"]["status"] == "unmeasured"
    assert doc["coverage"]["bars"] == 0
    assert doc["distribution"]["sessions"] == 0


def test_every_horizon_is_reported_even_when_only_some_are_measurable(tmp_path):
    conn = _cache(tmp_path, _live_shape(80))
    doc = mr.build(conn, "SPX", through="2026-09-30", generated_at="t")
    assert set(doc["horizons"]) == set(mr.HORIZONS)
    assert doc["horizons"]["session"]["status"] == "measured"
    assert doc["horizons"]["primary"]["status"] == "unmeasured", "200 sessions are not there"


def test_write_emits_the_dated_file_and_advances_latest_only_forward(tmp_path, monkeypatch):
    monkeypatch.setattr(mr, "_data_dir", lambda: tmp_path / "out")
    mr.write({"module": "marketregime", "session": "2026-09-22", "symbol": "SPX"})
    mr.write({"module": "marketregime", "session": "2026-09-18", "symbol": "SPX"})
    names = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert names == [
        "market_regime-2026-09-18.json",
        "market_regime-2026-09-22.json",
        "market_regime.json",
    ]
    import json

    latest = json.loads((tmp_path / "out" / "market_regime.json").read_text())
    assert latest["session"] == "2026-09-22", "an older re-cut never overwrites the newer latest"
