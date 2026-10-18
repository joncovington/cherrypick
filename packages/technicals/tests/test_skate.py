"""The skate screen's range band and its history (docs/skate-plan.md, step 1).

Each test pins one thing a row depends on. A band a tick too wide, or an expiry that quietly spans
an earnings report, reads exactly like a good candidate, so the failures here are the point.
"""

from __future__ import annotations

import json
import math
from datetime import date

import pytest
from cherrypick.core import calendar as cal

from cherrypick.technicals import adjust, paths, skate, store

CFG = skate.settings(None)
DAY = date(2026, 10, 16)


def test_the_config_example_states_the_defaults():
    example = json.loads(
        (paths.Path(__file__).parents[1] / "config.example.json").read_text(encoding="utf-8")
    )
    block = {k: v for k, v in example["skate"].items() if not k.startswith("_")}
    assert block == skate.DEFAULTS
    assert skate.settings(example["skate"]) == skate.DEFAULTS


# ------------------------------------------------------------------------------------------------
# The band.


def test_smoothed_range_is_the_average_of_highest_high_less_lowest_low():
    highs = [10, 12, 11, 13, 12, 14]
    lows = [9, 10, 9, 11, 10, 12]
    # n = 2: ranges from index 1 are 12-9, 12-9, 13-9, 13-10, 14-10 = 3, 3, 4, 3, 4
    out = skate.smoothed_range(highs, lows, 2, 3)
    assert out[:3] == [None, None, None]
    assert out[3:] == pytest.approx([10 / 3, 10 / 3, 11 / 3])


def test_the_call_band_sits_above_the_close_and_the_put_band_below():
    closes = [100.0, 101.0]
    srange = [None, 4.0]
    assert skate.band_series(closes, srange, 0.75, "C") == [None, 104.0]
    assert skate.band_series(closes, srange, 0.75, "P") == [None, 98.0]


def test_the_by_expiry_schedule_applies_only_to_its_own_band():
    assert skate.haircut_for(7, "by_expiry", CFG) == 0.65
    assert skate.haircut_for(14, "by_expiry", CFG) == 0.65
    assert skate.haircut_for(15, "by_expiry", CFG) == 0.75
    assert skate.haircut_for(7, "fixed", CFG) == 0.75


# ------------------------------------------------------------------------------------------------
# The skate curve.


def test_a_call_skates_when_the_later_close_stays_below_the_strike():
    ratios = [0.95, 0.99, 1.00, 1.01, 1.03]  # close(t+n) / band(t)
    curve = skate.skate_curve(ratios, "C", [0.0, 0.02, 0.05])
    # below 1.00: 2 of 5; below 1.02: 4 of 5; below 1.05: 5 of 5
    assert curve == [0.4, 0.8, 1.0]


def test_a_put_skates_when_the_later_close_stays_above_the_strike():
    ratios = [0.95, 0.99, 1.00, 1.01, 1.03]
    curve = skate.skate_curve(ratios, "P", [0.0, 0.02, 0.05])
    # above 1.00: 2 of 5; above 0.98: 4 of 5; above 0.95: 4 of 5 (0.95 itself is a breach)
    assert curve == [0.4, 0.8, 0.8]


def test_a_strike_further_past_the_band_never_skates_less_often():
    ratios = [1 + 0.03 * math.sin(i) for i in range(500)]
    for side in skate.SIDES:
        curve = skate.skate_curve(ratios, side, skate.offsets(CFG))
        assert curve == sorted(curve)


# ------------------------------------------------------------------------------------------------
# Expiries and earnings.


def test_expiries_are_the_weekly_fridays_inside_the_window():
    exps = skate.expiries(DAY, 63, 3)
    assert exps[0] == date(2026, 10, 23) and exps[-1] == date(2026, 12, 18)
    assert all(e.weekday() == 4 for e in exps) and len(exps) == 9


def test_a_holiday_friday_moves_the_expiry_to_the_thursday():
    # Good Friday 2027 is 26 March.
    assert date(2027, 3, 25) in skate.expiries(date(2027, 3, 12), 21, 3)
    assert date(2027, 3, 26) not in skate.expiries(date(2027, 3, 12), 21, 3)


def test_an_expiry_too_few_sessions_away_is_left_out():
    # Thursday 15 Oct to Friday 16 Oct is one session.
    assert date(2026, 10, 16) not in skate.expiries(date(2026, 10, 15), 63, 3)


def _series(days: int = 900, start: float = 100.0):
    """Trading days ending on DAY, a gently waving price."""
    dates = [DAY]
    while len(dates) < days:
        dates.append(cal.previous_trading_day(dates[-1]))
    dates.reverse()
    closes = [start * (1 + 0.08 * math.sin(i / 17)) for i in range(days)]
    return [d.isoformat() for d in dates], [c * 1.01 for c in closes], [c * 0.99 for c in closes], closes


def _judge(fund=False, earnings=date(2026, 12, 1), days=900, cfg=None):
    dates, highs, lows, closes = _series(days)
    return skate.judge(
        "XYZ", dates, highs, lows, closes, DAY, fund, earnings, cfg or {**CFG, "history_from": "2000-01-01"}
    )


def test_an_expiry_on_or_after_the_report_is_dropped_and_earlier_ones_kept():
    judged, why = _judge(earnings=date(2026, 11, 20))
    assert why is None
    kept = {r["expiry"] for r in judged["rows"]}
    assert "2026-11-13" in kept and "2026-11-20" not in kept and "2026-11-27" not in kept


def test_a_stock_with_no_known_report_date_is_left_out():
    assert _judge(earnings=None) == (None, "earnings date unknown")
    assert _judge(earnings=date(2026, 10, 1)) == (None, "earnings date unknown")


def test_a_fund_needs_no_report_date():
    judged, why = _judge(fund=True, earnings=None)
    assert why is None and judged["earnings"] is None
    assert len({r["expiry"] for r in judged["rows"]}) == 9


def test_a_name_without_a_bar_on_the_day_is_left_out():
    dates, highs, lows, closes = _series()
    judged = skate.judge("XYZ", dates[:-1], highs[:-1], lows[:-1], closes[:-1], DAY, True, None, CFG)
    assert judged == (None, "no bar on the day")


def test_a_short_history_is_left_out():
    assert _judge(fund=True, days=200)[1] == "under a year of history"


def test_every_row_carries_both_bands_and_says_when_they_match():
    judged, _ = _judge(fund=True, earnings=None)
    short = next(r for r in judged["rows"] if r["days"] <= 14)
    long_ = next(r for r in judged["rows"] if r["days"] > 14)
    assert short["bands"]["by_expiry"]["haircut"] == 0.65 and not short["same_band"]
    assert long_["same_band"]
    for band in skate.BANDS:
        assert len(short["bands"][band]["skate"]) == len(skate.offsets(CFG))


# ------------------------------------------------------------------------------------------------
# Prices: splits applied, dividends not.


def test_price_bars_apply_a_split_but_not_a_dividend(tmp_path):
    conn = store.connect(tmp_path / "h.db")
    rows = [("XYZ", d, 100.0, 101.0, 99.0, 100.0, 1e6) for d in ("2026-10-12", "2026-10-13")]
    rows += [("XYZ", d, 50.0, 50.5, 49.5, 50.0, 2e6) for d in ("2026-10-14", "2026-10-15")]
    store.upsert_bars(conn, rows)
    store.upsert_splits(conn, [("XYZ", "2026-10-14", 2.0, 1.0)])
    store.upsert_dividends(conn, [("XYZ", "2026-10-15", 1.0)])
    conn.commit()
    closes = [b.close for b in skate.price_bars(conn, "XYZ")]
    assert closes == [50.0, 50.0, 50.0, 50.0]  # split-adjusted, no dividend scaling
    divs = [adjust.Dividend("2026-10-15", 1.0)]
    assert adjust.adjust(store.raw_bars(conn, "XYZ"), [], divs)[0].close != 100.0  # the scaling it skips


# ------------------------------------------------------------------------------------------------
# The declared leveraged-fund list and the build.


def test_the_declared_list_holds_the_funds_it_was_built_to_catch():
    funds = skate.leveraged_funds()
    for sym, kind in (("TQQQ", "leveraged"), ("SQQQ", "inverse"), ("UVXY", "volatility"), ("SH", "inverse")):
        assert funds[sym]["kind"] == kind
    assert all(f.get("kind") and f.get("evidence") for f in funds.values())


def _write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_the_build_drops_a_listed_fund_and_judges_the_rest(tmp_path):
    db = tmp_path / "h.db"
    conn = store.connect(db)
    dates, highs, lows, closes = _series()
    for sym in ("TQQQ", "SPY", "XYZ"):
        store.upsert_bars(
            conn, [(sym, d, c, h, lo, c, 1e6) for d, h, lo, c in zip(dates, highs, lows, closes, strict=True)]
        )
    conn.commit()
    conn.close()
    mr = paths.market_report_dir()
    _write(
        mr / "liquidity" / "verdicts.json",
        {
            "names": {s: {"verdict": "tradable"} for s in ("TQQQ", "SPY", "XYZ")}
            | {"ILL": {"verdict": "illiquid"}}
        },
    )
    _write(
        mr / "iv" / "tastytrade.json", {"days": {DAY.isoformat(): {"XYZ": {"earnings_date": "2026-11-05"}}}}
    )
    _write(
        mr / "securities" / f"{DAY.isoformat()}.json",
        {
            "rows": [
                {"symbol": "TQQQ", "type": "Exchange Traded Fund"},
                {"symbol": "SPY", "type": "Exchange Traded Fund"},
                {"symbol": "XYZ", "type": "Stock"},
            ]
        },
    )
    result = skate.build(cfg={"history_from": "2000-01-01"}, db=db, out_dir=tmp_path / "out")
    assert result["ok"] and result["universe"] == 3
    doc = json.loads((tmp_path / "out" / f"{DAY.isoformat()}.json").read_text(encoding="utf-8"))
    assert doc["skipped"] == {"leveraged, inverse or volatility fund": ["TQQQ"]}
    assert set(doc["names"]) == {"SPY", "XYZ"}
    assert {r["expiry"] for r in doc["names"]["XYZ"]["rows"]} == {"2026-10-23", "2026-10-30"}
    assert len({r["expiry"] for r in doc["names"]["SPY"]["rows"]}) == 9
