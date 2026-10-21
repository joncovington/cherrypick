"""The skate screen's range band and its history (docs/skate-plan.md, step 1).

Each test pins one thing a row depends on. A band a tick too wide, or an expiry that quietly spans
an earnings report, reads exactly like a good candidate, so the failures here are the point.
"""

from __future__ import annotations

import json
import math
from datetime import date

import pytest
from cherrypick.core import bs
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


def test_just_over_a_year_keeps_the_short_expiries_and_drops_only_the_long_ones():
    # 300 bars: the one-week expiry has ~280 windows to judge it on, the nine-week one ~200.
    judged, why = _judge(fund=True, earnings=None, days=300)
    assert why is None
    kept = sorted({r["days"] for r in judged["rows"]})
    assert kept[0] <= 7 and kept[-1] < 63
    full, _ = _judge(fund=True, earnings=None)
    assert len({r["expiry"] for r in judged["rows"]}) < len({r["expiry"] for r in full["rows"]})


def test_every_type_that_never_reports_is_exempt_and_a_company_is_not():
    for kind in ("Exchange Traded Fund", "Exchange Traded Commodity", "Fund", "Index", "Structured Product"):
        assert kind in skate.FUND_TYPES  # IAU is an Exchange Traded Commodity
    for kind in ("Stock", "Depository Receipt", "Preferred Shares", "Units", None):
        assert kind not in skate.FUND_TYPES


def test_the_consoles_fund_types_are_ones_skate_exempts_too():
    """Driven off the console reader's own declaration, so the two cannot drift apart unseen."""
    import re
    from pathlib import Path

    ts = Path(__file__).resolve().parents[2] / "console" / "server" / "src" / "readers" / "screeners.ts"
    declared = re.search(r"const FUND_TYPES = new Set\(\[([^\]]*)\]\)", ts.read_text(encoding="utf-8"))
    assert declared, "screeners.ts no longer declares FUND_TYPES"
    kinds = set(re.findall(r'"([^"]+)"', declared.group(1)))
    assert kinds and kinds <= set(skate.FUND_TYPES)


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


def test_a_reference_fund_that_passes_the_liquidity_test_is_banded_too(tmp_path):
    # As the real verdicts file has it: SPY is judged under "reference", never among the names.
    db = tmp_path / "h.db"
    conn = store.connect(db)
    dates, highs, lows, closes = _series()
    for sym in ("SPY", "XLU", "XYZ"):
        store.upsert_bars(
            conn, [(sym, d, c, h, lo, c, 1e6) for d, h, lo, c in zip(dates, highs, lows, closes, strict=True)]
        )
    conn.commit()
    conn.close()
    mr = paths.market_report_dir()
    _write(
        mr / "liquidity" / "verdicts.json",
        {
            "names": {"XYZ": {"verdict": "tradable"}},
            "reference": {"SPY": {"verdict": "tradable"}, "XLU": {"verdict": "illiquid"}},
        },
    )
    _write(
        mr / "iv" / "tastytrade.json", {"days": {DAY.isoformat(): {"XYZ": {"earnings_date": "2026-11-05"}}}}
    )
    _write(
        mr / "securities" / f"{DAY.isoformat()}.json",
        {"rows": [{"symbol": "SPY", "type": "Exchange Traded Fund"}, {"symbol": "XYZ", "type": "Stock"}]},
    )
    result = skate.build(cfg={"history_from": "2000-01-01"}, db=db, out_dir=tmp_path / "out")
    assert result["ok"] and result["universe"] == 2
    doc = json.loads((tmp_path / "out" / f"{DAY.isoformat()}.json").read_text(encoding="utf-8"))
    assert set(doc["names"]) == {"SPY", "XYZ"}  # a reference fund that fails the test stays out


# ------------------------------------------------------------------------------------------------
# Planning the quotes and scoring them (steps 2 and 3).


def test_the_grid_reaches_inside_the_band_for_the_closer_strike():
    grid = skate.offsets(CFG)
    assert grid[0] == -0.02 and 0.0 in grid and grid[-1] == 0.05


def test_the_monthly_is_the_third_friday_or_the_day_before_a_holiday():
    assert skate.is_monthly(date(2026, 11, 20)) and not skate.is_monthly(date(2026, 11, 27))
    assert skate.is_monthly(date(2025, 4, 17))  # Good Friday 2025 was the third Friday


def test_the_cycle_monthly_prefers_45_to_60_days_then_30_to_60():
    exps = ["2026-10-23", "2026-11-20", "2026-11-27", "2026-12-18"]
    assert skate.cycle_expiry(exps, date(2026, 10, 19)) == "2026-12-18"  # 60 days
    assert skate.cycle_expiry(exps, date(2026, 10, 16)) == "2026-11-20"  # 12-18 is 63 days out
    assert skate.cycle_expiry(["2026-10-23"], date(2026, 10, 16)) is None


def test_the_pick_is_the_first_strike_past_the_band_and_its_neighbours_are_named():
    ks = [95.0, 100.0, 105.0, 110.0]
    assert skate.snap(101.2, "C", ks) == 105.0 and skate.snap(101.2, "P", ks) == 100.0
    assert skate.snap(111.0, "C", ks) is None
    assert skate.around(105.0, "C", ks) == [("closer", 100.0), ("pick", 105.0), ("further", 110.0)]
    assert skate.around(100.0, "P", ks) == [("closer", 105.0), ("pick", 100.0), ("further", 95.0)]


def test_the_rate_at_a_strike_is_read_off_the_curve_and_clamped_outside_it():
    grid = [0.0, 0.01, 0.02]
    curve = [0.80, 0.90, 0.95]
    assert skate.curve_at(curve, grid, 0.005) == (pytest.approx(0.85), False)
    assert skate.curve_at(curve, grid, 0.03) == (0.95, True)
    assert skate.curve_at(curve, grid, -0.01) == (0.80, True)


def test_the_hard_filter_wants_a_tight_two_sided_quote_with_open_interest():
    hard = CFG["hard"]
    assert skate.hard_pass({"bid": 1.00, "ask": 1.08, "open_interest": 500}, hard)
    assert skate.hard_pass({"bid": 0.20, "ask": 0.25, "open_interest": 500}, hard)  # a nickel wide
    assert not skate.hard_pass({"bid": 1.00, "ask": 1.30, "open_interest": 500}, hard)
    assert not skate.hard_pass({"bid": 1.00, "ask": 1.05, "open_interest": 50}, hard)
    assert not skate.hard_pass({"bid": 0.0, "ask": 0.05, "open_interest": 500}, hard)


def _row(side="C", band=105.0):
    grid = skate.offsets(CFG)
    curve = [round(0.80 + 0.002 * i, 4) for i in range(len(grid))]
    b = {"haircut": 0.75, "band": band, "n": 2000, "n_recent": 700, "skate": curve, "skate_recent": curve}
    return {
        "side": side,
        "expiry": "2026-11-20",
        "days": 35,
        "sessions": 25,
        "range": 6.0,
        "same_band": True,
        "bands": {"fixed": b, "by_expiry": dict(b)},
    }


def test_a_scored_leg_carries_history_implied_odds_and_yield():
    g = skate.score_leg(
        _row(),
        "fixed",
        105.0,
        {"bid": 1.00, "ask": 1.10, "open_interest": 400},
        100.0,
        35,
        35 / 365,
        0.0,
        CFG,
        skate.offsets(CFG),
    )
    assert g["offset"] == 0.0 and g["skate"] == pytest.approx(0.816)
    assert 0 < g["delta"] < 0.5
    # Judged at expiry, so the implied odds are 1 - N(d2), and for a call that is safer than 1 - delta.
    assert g["implied_skate"] == pytest.approx(
        1 - bs.prob_itm(100.0, 105.0, 35 / 365, g["iv"], "C", CFG["rate"]), abs=1e-3
    )
    assert g["implied_skate"] > 1 - g["delta"] + 0.005
    assert g["gap"] == pytest.approx(g["skate"] - g["implied_skate"], abs=1e-4)
    assert g["yield_bid"] == pytest.approx(1.00 / 100 * 365 / 35, abs=1e-4)  # a call's capital is the shares
    assert g["per_day_bid"] == pytest.approx(100 / 35, abs=1e-3) and g["hard"]
    assert g["tested_per_month"] == pytest.approx(1 - 0.816, abs=1e-3)


def test_the_monthly_test_odds_compound_the_cycle_odds_over_a_month():
    # A 7-day option skating 81.6% a cycle is run five times in a 35-day month: tested 1 - 0.816^5.
    # (At 35 days the exponent is 1, which is why the test above cannot see a wrong one.)
    g = skate.score_leg(
        {**_row(), "days": 7},
        "fixed",
        105.0,
        {"bid": 0.30, "ask": 0.34, "open_interest": 400},
        100.0,
        7,
        7 / 365,
        0.0,
        CFG,
        skate.offsets(CFG),
    )
    assert g["tested_per_cycle"] == pytest.approx(1 - 0.816, abs=1e-3)
    assert g["tested_per_month"] == pytest.approx(1 - 0.816**5, abs=1e-3)


def _both(symbol, side, passes):
    leg = {"band": "fixed", "role": "pick", "expiry": "2026-11-20", "strike": 100.0, "hard": True}
    leg["soft"] = {"all": passes}
    best = {"fixed": {"expiry": "2026-11-20", "strike": 100.0}, "by_expiry": None}
    return {"symbol": symbol, "side": side, "best": best, "legs": [leg]}


def test_both_sides_is_marked_only_when_the_call_and_the_put_both_pass():
    rows = [
        _both("XYZ", "C", True),
        _both("XYZ", "P", True),
        _both("ABC", "C", True),
        _both("ABC", "P", False),
    ]
    skate.mark_both_sides(rows)
    assert [r["both_sides"]["fixed"] for r in rows] == [True, True, False, False]
    assert not any(r["both_sides"]["by_expiry"] for r in rows)  # no best on that band


def test_a_put_yields_on_the_strike_it_secures():
    g = skate.score_leg(
        _row("P", 95.0),
        "fixed",
        95.0,
        {"bid": 0.80, "ask": 0.85, "open_interest": 400},
        100.0,
        35,
        35 / 365,
        0.0,
        CFG,
        skate.offsets(CFG),
    )
    assert g["yield_bid"] == pytest.approx(0.80 / 95 * 365 / 35, abs=1e-4) and g["delta"] < 0
    # A put is the other way round: finishing past the strike is likelier than |delta| says.
    assert g["implied_skate"] < 1 - abs(g["delta"]) - 0.005


def _doc():
    return {
        "day": "2026-10-16",
        "offsets": skate.offsets(CFG),
        "names": {
            "XYZ": {
                "spot": 100.0,
                "fund": False,
                "earnings": "2026-12-03",
                "div_yield": 0.01,
                "rows": [_row("C"), {**_row("C"), "expiry": "2026-10-30", "days": 14}],
            }
        },
    }


CHAINS = {
    "XYZ": [
        {
            "expiration": e,
            "dte": d,
            "strikes": [[k, f"XYZ C{e}{k}", f"XYZ P{e}{k}"] for k in (100.0, 105.0, 110.0)],
        }
        for e, d in (("2026-10-30", 14), ("2026-11-20", 35))
    ]
}


def test_stage_one_quotes_each_band_pick_and_its_neighbours_on_the_cycle_monthly():
    legs = skate.stage_one(_doc(), CHAINS, date(2026, 10, 16))
    assert {g["expiry"] for g in legs} == {"2026-11-20"}
    assert {g["strike"]: g["roles"]["fixed"] for g in legs} == {
        100.0: "closer",
        105.0: "pick",
        110.0: "further",
    }
    assert all(g["option"].startswith("XYZ C") for g in legs)


def test_the_band_moves_with_todays_price_before_a_strike_is_picked():
    # The band day closed at 100 with the call band at 105. Today XYZ trades at 104, so the band is
    # 109 and the pick moves from 105 (nearly at the money now) to 110.
    legs = skate.stage_one(_doc(), CHAINS, date(2026, 10, 16), {"XYZ": 104.0})
    picks = {g["strike"] for g in legs if g["roles"].get("fixed") == "pick"}
    assert picks == {110.0}
    # Without a live spot the band day's close stands.
    still = skate.stage_one(_doc(), CHAINS, date(2026, 10, 16), {})
    assert {g["strike"] for g in still if g["roles"].get("fixed") == "pick"} == {105.0}


def test_a_strike_is_scored_against_the_moved_band():
    row = _row("C", 105.0)
    args = (
        row,
        "fixed",
        110.0,
        {"bid": 1.0, "ask": 1.1, "open_interest": 400},
        104.0,
        35,
        35 / 365,
        0.0,
        CFG,
    )
    moved = skate.score_leg(*args, skate.offsets(CFG), 4.0)
    assert moved["band_level"] == pytest.approx(109.0)
    assert moved["offset"] == pytest.approx(110.0 / 109.0 - 1, abs=1e-5)
    still = skate.score_leg(*args, skate.offsets(CFG))
    assert still["offset"] == pytest.approx(110.0 / 105.0 - 1, abs=1e-5)
    assert moved["skate"] < still["skate"]  # closer to today's band, so less often skated


def test_the_scored_entry_names_the_close_its_bands_were_set_on():
    legs = skate.stage_one(_doc(), CHAINS, date(2026, 10, 16), {"XYZ": 104.0})
    clock = {"2026-11-20": (35, 35 / 365)}
    (entry,) = skate.score(_doc(), legs, {"XYZ": 104.0}, clock, CFG)
    assert entry["spot"] == 104.0 and entry["band_day_close"] == 100.0
    pick = next(g for g in entry["legs"] if g["role"] == "pick" and g["band"] == "fixed")
    assert pick["strike"] == 110.0 and pick["band_level"] == pytest.approx(109.0)


def test_stage_one_falls_back_to_the_last_expiry_before_a_report():
    doc = _doc()
    doc["names"]["XYZ"]["rows"] = [r for r in doc["names"]["XYZ"]["rows"] if r["expiry"] == "2026-10-30"]
    legs = skate.stage_one(doc, CHAINS, date(2026, 10, 16))
    assert {g["expiry"] for g in legs} == {"2026-10-30"}
    assert skate.stage_one_expiry([], date(2026, 10, 16)) is None


def test_stage_two_covers_every_expiry_of_the_picked_name_sides():
    legs = skate.stage_two(_doc(), CHAINS, [("XYZ", "C")])
    assert {g["expiry"] for g in legs} == {"2026-10-30", "2026-11-20"}
    assert skate.stage_two(_doc(), CHAINS, []) == []


def _scored(quotes):
    legs = skate.stage_two(_doc(), CHAINS, [("XYZ", "C")])
    for g in legs:
        g["quote"] = quotes.get((g["expiry"], g["strike"]))
    clock = {"2026-10-30": (14, 14 / 365), "2026-11-20": (35, 35 / 365)}
    return skate.score(_doc(), legs, {"XYZ": 100.0}, clock, CFG)


def test_the_best_expiry_is_the_pick_that_pays_most_per_day_among_tradable_ones():
    q = {
        ("2026-10-30", 105.0): {"bid": 0.50, "ask": 0.55, "open_interest": 300},
        ("2026-11-20", 105.0): {"bid": 1.00, "ask": 1.05, "open_interest": 900},
    }
    (entry,) = _scored(q)
    best = skate.best_expiries(entry, "2026-11-20")["fixed"]
    # 0.50 over 14 days beats 1.00 over 35 days per day: 3.571 vs 2.857 a contract.
    assert best["expiry"] == "2026-10-30" and best["vs_monthly"] == pytest.approx(1.25, abs=1e-3)


def test_an_untradable_short_expiry_never_wins_the_best_expiry():
    q = {
        ("2026-10-30", 105.0): {"bid": 0.50, "ask": 0.55, "open_interest": 5},
        ("2026-11-20", 105.0): {"bid": 1.00, "ask": 1.05, "open_interest": 900},
    }
    (entry,) = _scored(q)
    assert skate.best_expiries(entry, "2026-11-20")["fixed"]["expiry"] == "2026-11-20"


def test_stage_two_takes_the_widest_gaps_and_skips_untradable_name_sides():
    def entry(sym, gap, hard=True):
        return {"symbol": sym, "side": "C", "legs": [{"role": "pick", "hard": hard, "gap": gap}]}

    scored = [entry("A", 0.02), entry("B", 0.10), entry("C", 0.30, hard=False), entry("D", -0.05)]
    assert skate.select(scored, 2) == [("B", "C"), ("A", "C")]
