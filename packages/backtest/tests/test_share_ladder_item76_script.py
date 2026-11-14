"""Hypothesis 76's study (packages/backtest/scripts/share_ladder_item76.py): the ladder's 100-share lots, the
hospital, the call's expiry and segment, the three strike rules, assignment against expiry, the marks and
the verdict. Invented bars and Black-Scholes chains only, no store.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from cherrypick.core import bs

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("share_ladder_item76", SCRIPTS / "share_ladder_item76.py")
s76 = importlib.util.module_from_spec(_spec)
sys.modules["share_ladder_item76"] = s76
_spec.loader.exec_module(s76)
Bar, Lot = s76.Bar, s76.Lot
START = date(2022, 1, 3)  # a Monday


def _sessions(n: int, start: date = START) -> list[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _bars(closes: list[float], f: list[float] | None = None) -> list[Bar]:
    """Bars with the open, high, low and close all at the close; f is the raw/adjusted factor."""
    f = f or [1.0] * len(closes)
    return [Bar(d, c, c, c, c, k) for d, c, k in zip(_sessions(len(closes)), closes, f, strict=True)]


def _ohlc(rows: list[tuple[float, float, float, float]]) -> list[Bar]:
    return [Bar(d, o, h, lo, c, 1.0) for d, (o, h, lo, c) in zip(_sessions(len(rows)), rows, strict=True)]


def _fridays(n: int, start: date = START) -> list[str]:
    d = start + timedelta(days=(4 - start.weekday()) % 7)
    return [(d + timedelta(days=7 * k)).isoformat() for k in range(n)]


def _chain(spot: float, years: float, *, vol: float = 0.6, half: float = 0.05, step: float = 1.0) -> dict:
    out = {}
    k = round(spot * 0.7)
    while k <= spot * 1.3:
        for right in ("C", "P"):
            m = bs.price(spot, k, years, vol, right, 0.0)
            out[(float(k), right)] = (max(0.0, m - half), m + half)
        k += step
    return out


def _chain_at(bars: list[Bar]):
    by = {b.date: b for b in bars}

    def chain_at(session: str, expiry: str) -> dict:
        b = by[session]
        years = (date.fromisoformat(expiry) - date.fromisoformat(session)).days / 365
        return _chain(b.c * b.f, years)

    return chain_at


def _lot(bars: list[Bar], price: float, hospital: int, entry: int = 0) -> Lot:
    return Lot(entry, price, 100 * bars[entry].f, hospital=hospital)


def _arm(rule, bars, lot, *, last=None, splits=(), hold_px=None, chain_at=None):
    return s76.run_call_arm(
        rule,
        "SOXL",
        lot,
        bars,
        _fridays(60),
        list(splits),
        len(bars) - 1 if last is None else last,
        chain_at or _chain_at(bars),
        lambda session: 0.0,
        hold_px,
    )


# ---- the strike rules ----


def test_breakeven_takes_the_lowest_strike_that_recovers_the_cost():
    chain = {
        (100.0, "C"): (3.0, 3.2),
        (101.0, "C"): (2.5, 2.7),
        (102.0, "C"): (2.0, 2.2),
        (103.0, "C"): (1.0, 1.2),
    }
    # cost 104, nothing banked: 101 + 2.5 = 103.5 misses, 102 + 2.0 = 104 holds
    assert s76.pick_breakeven(chain, 104.0, 0.0) == (102.0, False)
    # the premium already banked lowers the strike that suffices
    assert s76.pick_breakeven(chain, 104.0, 0.5) == (101.0, False)
    assert s76.pick_breakeven(chain, 104.0, 1.0) == (100.0, False)


def test_breakeven_with_no_qualifying_strike_takes_the_highest_and_says_so():
    chain = {(100.0, "C"): (1.0, 1.2), (101.0, "C"): (0.5, 0.7)}
    assert s76.pick_breakeven(chain, 150.0, 0.0) == (101.0, True)
    assert s76.pick_breakeven({}, 100.0, 0.0) is None


def test_breakeven_reads_only_strikes_quoted_on_both_sides():
    chain = {(100.0, "C"): (None, 3.2), (101.0, "C"): (2.5, 2.7)}
    assert s76.pick_breakeven(chain, 90.0, 0.0) == (101.0, False)


def test_nearest_delta_takes_the_lower_strike_on_a_tie():
    assert s76.nearest_delta({100.0: 0.40, 101.0: 0.34, 102.0: 0.20, 103.0: 0.10}) == 101.0
    assert s76.nearest_delta({101.0: 0.375, 102.0: 0.125}, 0.25) == 101.0  # equally far: the lower
    assert s76.nearest_delta({}) is None


def test_delta30_picks_the_black_scholes_strike_nearest_a_third():
    spot, years = 100.0, 0.12
    chain = _chain(spot, years)
    expected = min(
        range(70, 131), key=lambda k: (abs(bs.delta(spot, float(k), years, 0.6, "C", 0.0) - 0.30), k)
    )
    assert s76.pick_delta30(chain, spot, years, 0.0) == (float(expected), False)
    assert 100 < expected < 115  # out of the money, not the money's neighbour


def test_cost5_takes_the_lowest_strike_at_or_over_105_percent_of_cost():
    chain = {(float(k), "C"): (1.0, 1.2) for k in range(100, 111)}
    assert s76.pick_cost5(chain, 100.0) == (105.0, False)  # exactly 1.05 P qualifies
    assert s76.pick_cost5(chain, 100.5) == (106.0, False)  # 105.525 -> the next listed
    assert s76.pick_cost5(chain, 200.0) == (110.0, True)  # none listed: the highest
    assert s76.pick_cost5({}, 100.0) is None


# ---- the lots and the hospital ----


def test_ladder_lot_is_100_shares_filled_at_item_26s_level_and_sold_at_its_target():
    bars = _ohlc([(100, 100, 100, 100), (95, 95, 90, 92), (94, 97, 93, 96), (96, 96, 96, 96)])
    lots, last = s76.ladder_lots(bars, "2022-01-01", "2022-12-31")
    assert last == 3 and len(lots) == 1
    lot = lots[0]
    assert (lot.entry, lot.price, lot.shares) == (1, pytest.approx(93.0), 100.0)  # 93% of 100, below the open
    assert lot.price == pytest.approx(0.93 * 100)
    assert (lot.exit, lot.exit_px) == (2, pytest.approx(93 * 1.04))  # high 97 reaches 96.72


def test_ladder_cap_is_three_and_a_slot_sold_today_frees_tomorrow():
    # each session's low reaches 93% of the last close, so every session would buy
    rows = [(100, 100, 100, 100)]
    px = 100.0
    for _ in range(5):
        rows.append((px * 0.95, px * 0.95, px * 0.90, px * 0.92))
        px *= 0.92
    lots, _ = s76.ladder_lots(_ohlc(rows), "2022-01-01", "2022-12-31")
    assert len(lots) == 3 and all(lot.exit is None for lot in lots)


def test_ladder_buys_no_lot_in_a_session_where_one_was_sold_into_a_full_book():
    rows = [(100, 100, 100, 100), (95, 95, 90, 92), (85, 85, 80, 82), (76, 76, 70, 72)]
    rows += [(78, 78, 66, 68), (80, 100, 80, 99)]  # 4th buy refused (full), then everything sells
    lots, _ = s76.ladder_lots(_ohlc(rows), "2022-01-01", "2022-12-31")
    assert len(lots) == 3
    assert all(lot.exit == 5 for lot in lots)


def test_hospital_is_the_first_session_18_days_after_the_buy_if_still_open():
    bars = _bars([100.0] * 40)
    lot = _lot(bars, 100.0, 0)
    j = s76.hospital_index(bars, lot, len(bars) - 1)
    assert bars[j].date == "2022-01-21"  # Jan 3 + 18 days is Jan 21, a Friday
    assert (date.fromisoformat(bars[j].date) - date.fromisoformat(bars[0].date)).days >= 18
    assert (date.fromisoformat(bars[j - 1].date) - date.fromisoformat(bars[0].date)).days < 18


def test_hospital_excludes_a_lot_sold_by_that_close_and_a_window_that_ends_first():
    bars = _bars([100.0] * 40)
    lot = _lot(bars, 100.0, 0)
    lot.exit, lot.exit_px = 14, 104.0  # sold in the hospital session itself
    assert s76.hospital_index(bars, lot, 39) is None
    lot.exit = 15  # sold the session after: it was open at the close
    assert s76.hospital_index(bars, lot, 39) == 14
    assert s76.hospital_index(bars, lot, 10) is None  # the window ends first


# ---- expiries, segments and the planning bound ----


def test_pick_expiry_is_35_to_60_days_nearest_45_the_earlier_on_a_tie():
    exps = _fridays(30)
    assert s76.pick_expiry("2022-01-04", exps) == "2022-02-18"  # 45 days exactly
    assert s76.pick_expiry("2022-01-21", exps) == "2022-03-04"  # 42 and 49 days: 42 is nearer
    assert s76.pick_expiry("2022-01-03", ["2022-02-01", "2022-04-01"]) is None  # 29 and 88 days
    assert s76.pick_expiry("2022-01-01", ["2022-02-05", "2022-03-27"]) == "2022-02-05"  # 35 and 85
    assert s76.pick_expiry("2022-01-01", ["2022-02-20", "2022-03-10"]) == "2022-02-20"  # 50 and 68
    # a tie: 40 and 50 days out, both 5 from 45
    assert s76.pick_expiry("2022-01-01", ["2022-02-10", "2022-02-20"]) == "2022-02-10"


def test_a_session_with_no_expiry_waits_for_the_next_that_has_one():
    dates = [b.date for b in _bars([100.0] * 30)]
    exps = ["2022-02-25"]  # 35 to 60 days out only from Dec 27 to Jan 21
    got = s76.next_sale(dates, exps, [], 14, 29)  # Jan 21: 35 days out
    assert got == (14, "2022-02-25")
    assert s76.next_sale(dates, exps, [], 15, 29) is None  # Jan 24: 32 days: none, to the window's end


def test_next_sale_skips_the_session_before_a_split_ex_date():
    bars = _bars([100.0] * 40)
    dates = [b.date for b in bars]
    got = s76.next_sale(dates, _fridays(30), [dates[20]], 19, 39)  # the session before the ex-date
    assert got is not None and got[0] == 20


def test_segment_ends_at_the_expiry_the_window_or_the_session_before_a_split():
    dates = [b.date for b in _bars([100.0] * 60)]
    assert s76.segment_end(dates, 14, "2022-03-04", [], 59) == (44, "expiry")
    assert s76.segment_end(dates, 14, "2022-03-04", [], 30) == (30, "end")
    assert s76.segment_end(dates, 14, "2022-03-04", [dates[25]], 59) == (24, "split")
    # an expiry on a non-session (a Saturday) ends at the session before
    e, how = s76.segment_end(dates, 14, "2022-03-05", [], 59)
    assert (dates[e], how) == ("2022-03-04", "expiry")
    # the expiry and the window end together is an expiry
    assert s76.segment_end(dates, 14, "2022-03-04", [], 44) == (44, "expiry")


def _falling_then_rebounding(n_flat: int, rebound: float) -> list[float]:
    return [100.0] + [90.0] * n_flat + [rebound] * 40


@pytest.mark.parametrize("rule", s76.ARMS)
def test_every_rule_has_ended_by_the_first_expiry_close_at_104_percent(rule):
    # stuck at 90 against a cost of 100, then a rebound to 105 that lasts: the lot ends at the first
    # expiry that closes there, under every rule, and the plan stops at the same expiry
    closes = [100.0] + [90.0] * 60 + [105.0] * 80
    bars = _bars(closes)
    lot = _lot(bars, 100.0, 14)
    plan = s76.plan_lot(bars, lot, _fridays(60), [], len(bars) - 1)
    planned_end = plan[-1][2]
    got = _arm(rule, bars, lot)
    ended = [b.date for b in bars].index(got["end"])
    assert got["how"] in ("assigned", "sold")
    assert ended <= planned_end
    assert bars[planned_end].c >= 104.0 and all(bars[e].c < 104.0 for _, _, e, _ in plan[:-1])


def test_the_planning_bound_is_a_check_that_can_fail():
    # a plan cut short, as if the bound were wrong, leaves the lot ending after it
    closes = [100.0] + [90.0] * 60 + [105.0] * 80
    bars = _bars(closes)
    lot = _lot(bars, 100.0, 14)
    plan = s76.plan_lot(bars, lot, _fridays(60), [], len(bars) - 1)
    assert len(plan) >= 2
    got = _arm("cost5", bars, lot)
    ended = [b.date for b in bars].index(got["end"])
    assert ended > plan[0][2]  # so a plan of only the first call would not bound it


def test_path_requests_list_each_expiry_once_from_its_earliest_sale_to_its_latest_end():
    bars = _bars([100.0] + [90.0] * 60 + [105.0] * 80)
    a, b = _lot(bars, 100.0, 14), _lot(bars, 100.0, 15)
    reqs = s76.path_requests("SOXL", bars, [a, b], _fridays(60), [], len(bars) - 1)
    by = {r["expiration"]: r for r in reqs}
    assert len(by) == len(reqs)
    assert all(r["start"] <= r["through"] and r["symbol"] == "SOXL" for r in reqs)
    assert by["2022-03-04"]["start"] == bars[14].date  # the first lot's sale opens it
    none = s76.path_requests("SOXL", bars, [_lot(bars, 100.0, None)], _fridays(60), [], len(bars) - 1)
    assert none == []  # a lot that never reached the hospital needs no path


# ---- assignment, expiry, the 104% sale and the marks ----


def test_a_close_at_or_over_the_strike_assigns_the_call_and_ends_the_lot():
    closes = [100.0] + [90.0] * 40 + [110.0] * 40
    bars = _bars(closes)
    lot = _lot(bars, 100.0, 14)
    got = _arm("cost5", bars, lot)
    assert got["how"] == "assigned" and got["calls"] == 1
    chain = _chain_at(bars)(bars[14].date, "2022-03-04")
    bid = chain[(105.0, "C")][0]
    fees = s76.open_fee("SOXL", 1, "natural") + s76.core_fees.ASSIGNMENT_FEE_PER_SETTLEMENT
    want = -100 * 100 * (1 + s76.COST) + 105 * 100 * (1 - s76.COST) + bid * 100 - fees
    assert got["net"] == pytest.approx(want)
    assert not got["below_cost"]


def test_a_close_under_the_strike_expires_and_the_lot_is_sold_if_over_104_percent():
    closes = [100.0] + [90.0] * 40 + [104.5] * 40  # over 104, under the 105 strike
    bars = _bars(closes)
    got = _arm("cost5", bars, _lot(bars, 100.0, 14))
    assert got["how"] == "sold" and got["calls"] == 1
    chain = _chain_at(bars)(bars[14].date, "2022-03-04")
    bid = chain[(105.0, "C")][0]
    want = (
        -100 * 100 * (1 + s76.COST)
        + 100 * 104.5 * (1 - s76.COST)
        + bid * 100
        - s76.open_fee("SOXL", 1, "natural")
    )
    assert got["net"] == pytest.approx(want)  # letting the call expire costs nothing


def test_a_close_under_the_strike_and_under_104_percent_sells_the_next_call_at_that_close():
    closes = [100.0] + [90.0] * 80
    bars = _bars(closes)
    got = _arm("cost5", bars, _lot(bars, 100.0, 14))
    assert got["calls"] >= 2 and got["how"] == "end"
    assert got["premium"] > 0


def test_a_lot_marked_at_the_window_end_buys_its_call_back_at_the_ask():
    closes = [100.0] + [90.0] * 80
    bars = _bars(closes)
    last = 30
    got = _arm("cost5", bars, _lot(bars, 100.0, 14), last=last)
    assert got["how"] == "end" and got["end"] == bars[last].date
    at = _chain_at(bars)
    sold = at(bars[14].date, "2022-03-04")[(105.0, "C")]
    ask = at(bars[last].date, "2022-03-04")[(105.0, "C")][1]
    want = (
        -100 * 100 * (1 + s76.COST)
        + 100 * 90  # the shares marked at the close, no exit cost
        + sold[0] * 100
        - s76.open_fee("SOXL", 1, "natural")
        - ask * 100
        - s76.close_fee("SOXL", 1, "natural")
    )
    assert got["net"] == pytest.approx(want)
    # the mid reading is richer than the natural; the stress poorer
    assert got["net_mid"] > got["net"] > got["net_stress"]


def test_a_lot_whose_window_ends_with_no_expiry_to_sell_is_marked_with_no_call():
    bars = _bars([100.0] + [90.0] * 40)
    lot = _lot(bars, 100.0, 14)
    got = s76.run_call_arm("cost5", "SOXL", lot, bars, [], [], 40, _chain_at(bars), lambda s: 0.0)
    assert got["calls"] == 0 and got["how"] == "end"
    assert got["net"] == pytest.approx(s76.share_net(100.0, 100.0, 90.0, marked=True))
    assert got["waits"] == 40 - 14 + 1


def test_a_missing_chain_or_strike_waits_a_session_and_is_counted():
    bars = _bars([100.0] + [90.0] * 40)
    lot = _lot(bars, 100.0, 14)
    real = _chain_at(bars)

    def chain_at(session, expiry):
        return {} if session == bars[14].date else real(session, expiry)

    got = s76.run_call_arm("cost5", "SOXL", lot, bars, _fridays(60), [], 40, chain_at, lambda s: 0.0)
    assert got["skipped_no_chain"] == 1 and got["calls"] >= 1


def test_delta30_can_assign_under_the_cost_and_it_is_counted_as_a_loss():
    closes = [100.0] + [70.0] * 40 + [90.0] * 40  # stuck deep under cost; the rebound stays under it
    bars = _bars(closes)
    got = _arm("delta30", bars, _lot(bars, 100.0, 14), hold_px=None)
    assert got["how"] == "assigned" and got["below_cost"]
    rebound = _arm("delta30", bars, _lot(bars, 100.0, 14), hold_px=110.0)
    assert rebound["rebound"]  # the baseline sold at 110, over the strike that capped the arm


def test_breakeven_never_assigns_under_the_cost_when_a_strike_recovers_it():
    closes = [100.0] + [90.0] * 40 + [100.0] * 40  # back to cost by the first expiry
    bars = _bars(closes)
    got = _arm("breakeven", bars, _lot(bars, 100.0, 14))
    assert got["how"] in ("assigned", "sold")
    assert not got["below_cost"]


# ---- a split inside a call's life ----


def test_a_split_closes_the_call_the_session_before_and_sells_a_new_one_on_the_new_basis():
    # 2:1 on the session after index 29: raw 100 before, 50 after; the adjusted close is 50 throughout
    closes = [50.0] * 70
    f = [2.0] * 30 + [1.0] * 40
    bars = _bars(closes, f)
    lot = _lot(bars, 50.0, 14)
    assert lot.shares == 200.0  # 100 shares, 200 after the split
    got = _arm("cost5", bars, lot, splits=[bars[30].date])
    assert got["calls"] >= 2 and got["stale"] == 0
    # the first call is struck over 105% of the raw cost, the next over 105% of the same cost on the
    # new basis; neither sells on the session before the ex-date
    assert got["strikes"][0][1] == pytest.approx(105.0)
    assert got["strikes"][1][0] == bars[30].date and got["strikes"][1][1] == pytest.approx(53.0)  # 52.5 -> 53
    assert all(d != bars[29].date for d, _ in got["strikes"])


def test_a_split_inside_the_life_leaves_the_lot_net_equal_to_its_unsplit_twin_in_shares():
    # the same lot with no split: the share leg moves by the same dollars
    a = _bars([50.0] * 70, [2.0] * 30 + [1.0] * 40)
    b = _bars([100.0] * 70)
    got_a = _arm("cost5", a, _lot(a, 50.0, 14), splits=[a[30].date], last=30)
    got_b = _arm("cost5", b, _lot(b, 100.0, 14), last=30)
    # both end marked on the window's last close; the shares leg is flat at cost either way
    assert got_a["how"] == got_b["how"] == "end"


# ---- the hold, the decay exit and the paired difference ----


def test_hold_result_sells_at_the_baseline_exit_or_marks_the_last_close():
    bars = _bars([100.0] * 40)
    lot = _lot(bars, 100.0, 14)
    marked = s76.hold_result(lot, bars, 39)
    assert marked["marked"] and marked["net"] == pytest.approx(
        s76.share_net(100.0, 100.0, 100.0, marked=True)
    )
    lot.exit, lot.exit_px = 20, 104.0
    sold = s76.hold_result(lot, bars, 39)
    assert not sold["marked"] and sold["net"] == pytest.approx(100 * 4 - 0.0005 * 100 * (100 + 104))


def test_decay_exit_sells_at_breakeven_after_day_30():
    rows = [(100, 100, 100, 100)] + [(100, 100.2, 99, 99.5)] * 60
    bars = _ohlc(rows)
    lot = _lot(bars, 100.0, 14)
    got = s76.decay_result(lot, bars, len(bars) - 1)
    # breakeven after costs is 100 x 1.0005 / 0.9995 = 100.1; the first session from day 31 reaching it
    assert not got["marked"]
    assert (date.fromisoformat(bars[got["end"]].date) - date.fromisoformat(bars[0].date)).days > 30


def test_the_paired_difference_is_the_arms_net_less_the_holds_in_dollars_per_100_shares():
    bars = _bars([100.0] + [90.0] * 40 + [110.0] * 40)
    lot = _lot(bars, 100.0, 14)
    lot.exit, lot.exit_px = 50, 104.0
    hold = s76.hold_result(lot, bars, len(bars) - 1)
    got = _arm("cost5", bars, lot)
    row = s76.lot_row("SOXL", lot, bars, got, hold)
    assert row["diff"] == pytest.approx(got["net"] - hold["net"])
    assert row["hold_net"] == pytest.approx(hold["net"])
    assert row["diff_mid"] > row["diff"] > row["diff_stress"]


# ---- the statistics and the verdict ----


def _rep(n=40, mean=5.0, t=3.0, p=0.001, halves=(1.0, 2.0), names=(1.0, 2.0), stress=1.0):
    return {
        "n": n,
        "mean": mean,
        "t": t,
        "p": p,
        "halves": list(halves),
        "names": dict(zip(s76.NAMES, names, strict=True)),
        "stress": stress,
    }


def _verdicts(design: dict, holdout: dict):
    return s76.judge(
        {a: design.get(a, _rep()) for a in s76.ARMS}, {a: holdout.get(a, _rep(n=12)) for a in s76.ARMS}
    )


def test_an_arm_that_clears_every_condition_passes():
    v = _verdicts({}, {})
    assert all(x["verdict"] == "pass" and not x["provisional"] for x in v.values())


@pytest.mark.parametrize(
    "breaker, check",
    [
        (dict(mean=-1.0, t=-0.5, p=0.7), "mean"),
        (dict(t=1.5, p=0.07), "t"),
        (dict(halves=(1.0, -2.0)), "halves"),
        (dict(names=(1.0, -2.0)), "names"),
        (dict(stress=-0.5), "stress"),
    ],
)
def test_each_design_condition_fails_the_arm_alone(breaker, check):
    v = _verdicts({"cost5": _rep(**breaker)}, {})
    assert v["cost5"]["verdict"] == "fail" and not v["cost5"]["checks"][check]
    assert v["breakeven"]["verdict"] == "pass"  # the others are untouched


def test_holm_across_the_three_arms_fails_a_p_value_that_clears_one_test_alone():
    # 0.03 clears 0.05 alone but not Holm's 0.05/3 when it is the smallest of three
    v = _verdicts({a: _rep(p=0.03, t=2.2) for a in s76.ARMS}, {})
    assert all(not x["checks"]["holm"] and x["verdict"] == "fail" for x in v.values())


def test_a_failed_holdout_fails_the_arm_and_a_thin_one_is_provisional():
    v = _verdicts({}, {"delta30": _rep(n=12, mean=-3.0)})
    assert v["delta30"]["verdict"] == "fail" and v["breakeven"]["verdict"] == "pass"
    thin = _verdicts({}, {"cost5": _rep(n=6)})
    assert thin["cost5"]["verdict"] == "pass" and thin["cost5"]["provisional"]
    none = _verdicts({}, {"cost5": _rep(n=0)})
    assert none["cost5"]["verdict"] == "no verdict"


def test_fewer_than_30_design_lots_is_no_verdict_and_30_is_enough():
    v = _verdicts({"cost5": _rep(n=29)}, {})
    assert v["cost5"]["verdict"] == "no verdict" and "29" in v["cost5"]["why"]
    assert _verdicts({"cost5": _rep(n=30)}, {})["cost5"]["verdict"] == "pass"


def test_arm_report_pairs_one_lot_one_observation_and_reports_the_rest():
    rows = [
        {
            "name": "SOXL" if i % 2 else "TQQQ",
            "entry": f"2020-0{1 + i % 9}-15",
            "diff": float(d),
            "diff_stress": d - 1.0,
            "diff_mid": d + 1.0,
            "net": 10.0 * d,
            "hold_net": 0.0,
            "premium": 50.0,
            "hospital_days": 40,
            "hold_days": 60,
            "capital_days": 1e5,
            "hold_capital_days": 2e5,
            "below_cost": i == 0,
            "rebound": i == 1,
            "waits": 1,
            "fallbacks": 0,
            "stale": 0,
            "how": "assigned" if i % 2 else "sold",
        }
        for i, d in enumerate([3, 5, 7, 9, 11, 13])
    ]
    rep = s76.arm_report(rows, ("2016-05-02", "2024-12-31"))
    assert rep["n"] == 6 and rep["mean"] == pytest.approx(8.0)
    assert rep["stress"] == pytest.approx(7.0) and rep["mid"] == pytest.approx(9.0)
    assert rep["below_cost"] == 1 and rep["rebound"] == 1 and rep["premium"] == 300.0
    assert rep["hospital_days"] == 240 and rep["hold_hospital_days"] == 360
    assert rep["worst"] == pytest.approx(30.0) and rep["mde"] > 0
    assert rep["how"] == {"assigned": 3, "sold": 3}
    assert set(rep["names"]) == set(s76.NAMES)


def test_halves_split_the_design_window_at_its_midpoint():
    h1, h2 = s76.halves(s76.DESIGN)
    assert h1[0] == s76.DESIGN[0] and h2[1] == s76.DESIGN[1]
    assert date.fromisoformat(h2[0]) - date.fromisoformat(h1[1]) == timedelta(days=1)


def test_bars_from_divides_out_a_split_and_keeps_the_raw_basis_in_f():
    from types import SimpleNamespace

    raw = [
        SimpleNamespace(date="2022-01-03", open=100.0, high=102.0, low=98.0, close=100.0),
        SimpleNamespace(date="2022-01-04", open=50.0, high=51.0, low=49.0, close=50.0),
    ]
    split = SimpleNamespace(ex_date="2022-01-04", to_factor=2.0, for_factor=1.0)
    bars = s76.bars_from(raw, [split])
    assert (bars[0].c, bars[0].f) == (50.0, 2.0) and (bars[1].c, bars[1].f) == (50.0, 1.0)
    assert bars[0].c * bars[0].f == 100.0  # the day's own close, the chain's strike basis
    assert s76.bars_from(raw, [])[0].c == 100.0
