"""Item 102's script (ladder_repair_item102.py): the ratio-call-spread repair on the ladder's stuck lots.
Invented bars and chains only, the store never read. Each rule is shown firing and failing, because a check
that cannot fire reads as coverage it is not."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from cherrypick.core import bs

from cherrypick.backtest.study import rule as kit_rule

_spec = importlib.util.spec_from_file_location(
    "ladder_repair_item102", Path(__file__).resolve().parents[1] / "scripts" / "ladder_repair_item102.py"
)
m = importlib.util.module_from_spec(_spec)
sys.modules["ladder_repair_item102"] = m  # its dataclasses look their module up by name
_spec.loader.exec_module(m)
Bar, Lot = m.Bar, m.Lot
START = date(2022, 1, 3)  # a Monday


def _sessions(n: int) -> list[str]:
    out, d = [], START
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _bars(closes: list[float]) -> list[Bar]:
    return [Bar(d, c, c, c, c, 1.0) for d, c in zip(_sessions(len(closes)), closes, strict=True)]


def _on_or_after(day: str) -> str:
    d = date.fromisoformat(day)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


def test_the_declared_rule_is_complete():
    kit_rule.check_rule(m.RULE)
    assert m.RULE["splits"] == ["2025-01-01"]
    assert (m.RULE["min_n"], m.RULE["holdout_min_n"]) == (30, 10)


def test_the_chain_samples_smokes_then_fetches_and_studies():
    assert [s["step"] for s in m.CHAIN] == ["plan", "fetch-sample", "smoke", "fetch-chains", "study"]


# ---------------------------------------------------------------------------------- the strike rules

CHAIN = {  # close 20: (bid, ask) of each call
    (18.0, "C"): (2.9, 3.1),
    (20.0, "C"): (1.9, 2.1),
    (22.0, "C"): (1.1, 1.2),
    (24.0, "C"): (0.6, 0.7),
    (26.0, "C"): (0.3, 0.4),
    (28.0, "C"): (0.1, 0.2),
    (30.0, "C"): (0.0, 0.05),
}


def test_repair_atm_longs_the_nearest_strike_and_shorts_the_highest_one_that_pays_for_it():
    # 2 x bid(22) = 2.2 covers ask(20) = 2.1; 2 x bid(24) = 1.2 does not
    assert m.pick_repair_atm(CHAIN, 20.3, 0.05) == (20.0, 22.0)


def test_repair_atm_takes_the_lower_strike_on_a_tie_and_takes_a_small_debit_but_no_more():
    assert m.pick_repair_atm(CHAIN, 21.0, 0.05)[0] == 20.0
    # ask(20) - 2 bid(24) = 0.9: a debit bound of 0.95 reaches it, 0.85 does not
    assert m.pick_repair_atm(CHAIN, 20.0, 0.95)[1] == 24.0
    assert m.pick_repair_atm(CHAIN, 20.0, 0.85)[1] == 22.0


def test_repair_atm_picks_nothing_when_no_strike_pays_for_it():
    thin = {k: (b * 0.3, a) for k, (b, a) in CHAIN.items()}
    assert m.pick_repair_atm(thin, 20.0, 0.05) is None
    assert m.pick_repair_atm({}, 20.0, 0.05) is None


def test_repair_atm_never_shorts_a_strike_with_no_bid():
    chain = {(20.0, "C"): (0.0, 0.05), (22.0, "C"): (0.0, 0.05)}
    assert m.pick_repair_atm(chain, 20.0, 0.05) is None


def test_repair_cost_shorts_the_strike_nearest_cost_over_the_close_and_longs_the_lowest_it_pays_for():
    # cost 24.4 -> 24; ask must be at most 2 x 0.6 + 0.05 = 1.25: the 22 call (1.2), not the 20 (2.1)
    assert m.pick_repair_cost(CHAIN, 20.0, 24.4, 0.05) == (22.0, 24.0)
    # a looser bound reaches lower: 2.1 <= 1.2 + 0.9
    assert m.pick_repair_cost(CHAIN, 20.0, 24.4, 0.95) == (20.0, 24.0)


def test_repair_cost_ignores_strikes_at_or_under_the_close_and_waits_when_none_is_over():
    assert (
        m.pick_repair_cost(CHAIN, 20.0, 10.0, 0.05)[1] == 22.0
    )  # cost far under: the nearest over the close
    assert m.pick_repair_cost(CHAIN, 30.0, 25.0, 0.05) is None
    assert m.pick_repair_cost({(30.0, "C"): (0.0, 0.05), (28.0, "C"): (1.0, 1.1)}, 20.0, 30.0, 0.05) is None


def test_breakeven_is_item_76s_covered_call_rule():
    assert m.pick_breakeven(CHAIN, 23.0, 0.0) == (22.0, False)  # 22 + 1.1 >= 23
    assert m.pick_breakeven(CHAIN, 99.0, 0.0) == (30.0, True)  # none qualifies: the highest, flagged


# ---------------------------------------------------------------------------------- settlement


def test_above_the_short_strike_the_shares_go_there_and_the_vertical_pays_its_width():
    legs = m.unit_legs("repair", {"K1": 20.0, "K2": 24.0})
    res = m.settle(legs, 30.0)
    assert res["called"] and res["at"] == 24.0
    assert res["cash"] == pytest.approx(4.0)  # K2 - K1, whatever the close
    assert m.settle(legs, 50.0)["cash"] == pytest.approx(4.0)
    assert res["symbols"] == 2


def test_between_the_strikes_the_long_calls_intrinsic_is_banked_and_the_shares_stay():
    legs = m.unit_legs("repair", {"K1": 20.0, "K2": 24.0})
    res = m.settle(legs, 22.0)
    assert not res["called"] and res["cash"] == pytest.approx(2.0) and res["symbols"] == 1


def test_under_the_long_strike_everything_expires():
    legs = m.unit_legs("repair", {"K1": 20.0, "K2": 24.0})
    assert m.settle(legs, 18.0) == {"called": False, "at": None, "cash": 0.0, "symbols": 0}


def test_a_covered_call_is_assigned_at_or_over_its_strike_and_expires_under_it():
    legs = m.unit_legs("call", {"K": 25.0})
    assert m.settle(legs, 30.0) == {"called": True, "at": 25.0, "cash": 0.0, "symbols": 1}
    assert m.settle(legs, 24.0)["symbols"] == 0 and not m.settle(legs, 24.0)["called"]


# ---------------------------------------------------------------------------------- one lot, end to end


def _world(expiry_closes: dict[int, float], n: int = 220, price: float = 110.0):
    """A lot bought at `price` over 100-close bars, so it sits in the hospital; each call chain is priced by
    Black-Scholes off that session's close. `expiry_closes` maps a bar index to a close."""
    closes = [100.0] * n
    for k, c in expiry_closes.items():
        closes[k] = c
    bars = _bars(closes)
    lot = Lot(0, price, 100.0)
    lot.hospital = m.hospital_index(bars, lot, n - 1)
    dates = [b.date for b in bars]
    first = _on_or_after((date.fromisoformat(dates[lot.hospital]) + timedelta(days=45)).isoformat())
    second = _on_or_after((date.fromisoformat(first) + timedelta(days=45)).isoformat())
    expiries = [first, second]

    def chain_at(session: str, expiry: str):
        spot = closes[dates.index(session)]
        years = (date.fromisoformat(expiry) - date.fromisoformat(session)).days / 365
        out = {}
        for k in range(int(spot * 0.6), int(spot * 1.5) + 1):
            mid = bs.price(spot, float(k), years, 0.8, "C", 0.0)
            out[(float(k), "C")] = (max(0.0, mid - 0.02), mid + 0.02)
        return out

    return bars, lot, dates, expiries, chain_at


def test_a_lot_closing_above_the_short_strike_ends_there_with_the_vertical_banked():
    bars, lot, dates, expiries, chain_at = _world({})
    e1 = dates.index(max(d for d in dates if d <= expiries[0]))
    bars, lot, dates, expiries, chain_at = _world({e1: 160.0})
    got = m.run_arm("repair-atm", "SOXL", lot, bars, expiries, [], len(bars) - 1, chain_at)
    j = lot.hospital
    k1, k2 = m.pick_repair_atm(chain_at(dates[j], expiries[0]), 100.0, 0.05)
    q1, q2 = chain_at(dates[j], expiries[0])[(k1, "C")], chain_at(dates[j], expiries[0])[(k2, "C")]
    assert got["how"] == "assigned" and got["units"] == 1 and got["end"] == dates[e1]
    credit = (2 * q2[0] - q1[1]) * 100
    expected = (
        -100 * 110 * (1 + m.COST)
        + k2 * 100 * (1 - m.COST)
        + credit
        + (k2 - k1) * 100
        - m.open_fee("SOXL", "repair", 1.0, "natural")
        - 2 * m.core_fees.ASSIGNMENT_FEE_PER_SETTLEMENT
    )
    assert got["net"] == pytest.approx(expected)
    assert got["premium"] == pytest.approx(credit)


def test_a_lot_between_the_strikes_banks_the_intrinsic_and_reopens_when_it_is_still_short_of_its_target():
    bars, lot, dates, expiries, chain_at = _world({})
    e1 = dates.index(max(d for d in dates if d <= expiries[0]))
    e2 = dates.index(max(d for d in dates if d <= expiries[1]))
    # the lot's cost is 110 and its target 114.4: the close of 103 plus the intrinsic is well short of it
    bars, lot, dates, expiries, chain_at = _world({e1: 103.0, e2: 170.0})
    j = lot.hospital
    k1, k2 = m.pick_repair_atm(chain_at(dates[j], expiries[0]), 100.0, 0.05)
    assert k1 < 103.0 < k2  # the close lands between the strikes
    got = m.run_arm("repair-atm", "SOXL", lot, bars, expiries, [], len(bars) - 1, chain_at)
    assert got["units"] == 2 and got["how"] == "assigned" and got["end"] == dates[e2]


def test_a_lot_under_the_long_strike_lets_the_options_expire_and_reopens():
    bars, lot, dates, expiries, chain_at = _world({})
    e1 = dates.index(max(d for d in dates if d <= expiries[0]))
    e2 = dates.index(max(d for d in dates if d <= expiries[1]))
    bars, lot, dates, expiries, chain_at = _world({e1: 80.0, e2: 170.0})
    got = m.run_arm("repair-atm", "SOXL", lot, bars, expiries, [], len(bars) - 1, chain_at)
    assert got["units"] == 2 and got["how"] == "assigned"


def test_a_lot_whose_close_plus_banked_cash_reaches_104_percent_of_cost_is_sold_at_the_close():
    bars, lot, dates, expiries, chain_at = _world({}, price=96.0)
    e1 = dates.index(max(d for d in dates if d <= expiries[0]))
    # a lot costing 96 has a target of 99.84; at 80 it is far short, so use a close over the target but
    # under the long strike (the strikes sit near 100): the options expire and the shares sell
    bars, lot, dates, expiries, chain_at = _world({e1: 99.9}, price=96.0)
    j = lot.hospital
    k1, _ = m.pick_repair_atm(chain_at(dates[j], expiries[0]), 100.0, 0.05)
    assert k1 > 99.9
    got = m.run_arm("repair-atm", "SOXL", lot, bars, expiries, [], len(bars) - 1, chain_at)
    assert got["how"] == "sold" and got["units"] == 1 and got["end"] == dates[e1]


def test_a_close_under_the_target_with_no_banked_cash_does_not_sell():
    bars, lot, dates, expiries, chain_at = _world({}, price=96.0)
    e1 = dates.index(max(d for d in dates if d <= expiries[0]))
    e2 = dates.index(max(d for d in dates if d <= expiries[1]))
    bars, lot, dates, expiries, chain_at = _world({e1: 80.0, e2: 99.0}, price=96.0)
    got = m.run_arm("repair-atm", "SOXL", lot, bars, expiries, [], len(bars) - 1, chain_at)
    assert got["units"] >= 2  # 80 is far short of 99.84: reopened


def test_a_lot_waits_for_a_session_with_a_chain_and_counts_the_wait():
    bars, lot, dates, expiries, chain_at = _world({})
    j = lot.hospital

    def late(session, expiry):
        return None if session < dates[j + 3] else chain_at(session, expiry)

    got = m.run_arm("repair-atm", "SOXL", lot, bars, expiries, [], len(bars) - 1, late)
    assert got["skipped_no_chain"] == 3 and got["waits"] >= 3


def test_a_waiting_lot_sells_where_the_baseline_sells_it_and_an_open_unit_is_held_through_it():
    bars, lot, dates, expiries, chain_at = _world({})
    j = lot.hospital
    lot.exit, lot.exit_px = j + 3, 114.4  # the baseline's target is met three sessions into the wait

    def late(session, expiry):
        return None if session < dates[j + 5] else chain_at(session, expiry)

    got = m.run_arm("repair-atm", "SOXL", lot, bars, expiries, [], len(bars) - 1, late)
    assert got["how"] == "held" and got["units"] == 0 and got["end"] == dates[j + 3]
    assert got["net"] == pytest.approx(m.share_net(lot.shares, lot.price, 114.4))
    # the same exit with the chain there at the hospital close: the unit is open, so the exit is not taken
    held = m.run_arm("repair-atm", "SOXL", lot, bars, expiries, [], len(bars) - 1, chain_at)
    assert held["units"] >= 1 and held["how"] != "held"


def test_a_lot_open_at_the_windows_end_is_marked_and_its_options_closed():
    bars, lot, dates, expiries, chain_at = _world({}, n=40)
    got = m.run_arm("repair-atm", "SOXL", lot, bars, expiries, [], len(bars) - 1, chain_at)
    assert got["how"] == "end" and got["end"] == dates[-1] and got["units"] == 1
    # marked: no exit cost on the shares, and the options closed at their last quotes (the same chain, so the
    # round trip costs the spreads and fees only)
    assert got["net"] < got["net_mid"] + 1e-9


def test_the_covered_call_arm_is_item_76s_rule_and_counts_a_call_away_below_cost():
    bars, lot, dates, expiries, chain_at = _world({}, price=130.0)
    e1 = dates.index(max(d for d in dates if d <= expiries[0]))
    bars, lot, dates, expiries, chain_at = _world({e1: 200.0}, price=130.0)
    got = m.run_arm("breakeven", "SOXL", lot, bars, expiries, [], len(bars) - 1, chain_at)
    assert got["how"] == "assigned" and got["units"] == 1


def test_a_split_inside_a_units_life_closes_it_the_session_before_and_opens_the_next_on_the_ex_date():
    bars, lot, dates, expiries, chain_at = _world({})
    ex = dates[lot.hospital + 10]
    got = m.run_arm("repair-atm", "SOXL", lot, bars, expiries, [ex], len(bars) - 1, chain_at)
    assert got["units"] >= 2  # the first unit is closed at the ex-date's eve, the next opened on the ex-date
    assert got["stale"] == 0
    # round trips at the ask and the bid cost the spreads and fees, so the paired result is under the mid's
    assert got["net"] < got["net_mid"]


def test_a_missing_declared_path_is_counted_not_read():
    class Inputs:
        def chain(self, conn, sym, session, expiry):
            raise m.Undeclared("not requested")

    undeclared: list = []
    chain_at = m.make_chain_at(Inputs(), None, "SOXL", undeclared)
    assert chain_at("2022-02-01", "2022-03-18") is None
    assert undeclared == [("SOXL", "2022-02-01", "2022-03-18")]


# ---------------------------------------------------------------------------------- the lots and the plan


def test_the_ladder_buys_seven_percent_under_and_sells_four_percent_up_at_a_cap_of_three():
    closes = [100, 92, 91, 90, 89, 88, 88, 88]
    rows = [(c, c, c * 0.999, c) for c in closes]
    bars = [Bar(d, o, h, lo, c, 1.0) for d, (o, h, lo, c) in zip(_sessions(len(rows)), rows, strict=True)]
    lots, last = m.ladder_lots(bars, bars[0].date, bars[-1].date)
    assert last == len(bars) - 1
    assert 1 <= len(lots) <= m.CAP
    assert lots[0].price == 92.0  # the second session opens under the 93 level, so the open is the fill


def test_the_hospital_is_the_first_session_eighteen_days_on_while_the_lot_is_open():
    bars = _bars([100.0] * 40)
    lot = Lot(0, 90.0, 100.0)
    j = m.hospital_index(bars, lot, 39)
    assert m._cal(bars[0].date, bars[j].date) >= 18 > m._cal(bars[0].date, bars[j - 1].date)
    lot.exit = j  # sold in the hospital session: never in the hospital
    assert m.hospital_index(bars, lot, 39) is None
    lot.exit = j + 1
    assert m.hospital_index(bars, lot, 39) == j


def test_the_plan_requests_the_expiry_a_waiting_lot_would_sell_on_as_well():
    bars, lot, dates, expiries, _ = _world({})
    sooner = _on_or_after((date.fromisoformat(dates[lot.hospital]) + timedelta(days=60)).isoformat())
    listed = sorted({*expiries, sooner})
    paths = m.lot_paths(bars, lot, listed, [], len(bars) - 1)
    assert {p[0] for p in paths} >= {expiries[0]}
    nominal = m.plan_lot(bars, lot, listed, [], len(bars) - 1)
    assert len(paths) > len(nominal)  # the waits add paths
    wait_free = [p for p in paths if p[1] > dates[lot.hospital]]
    assert wait_free


def test_one_path_a_name_and_expiry_spans_every_lot_that_uses_it():
    bars, lot, dates, expiries, _ = _world({})
    reqs = m.path_requests("SOXL", bars, [lot], expiries, [], len(bars) - 1)
    assert all(set(r) == {"symbol", "expiration", "start", "through"} for r in reqs)
    assert len({(r["symbol"], r["expiration"]) for r in reqs}) == len(reqs)


# ---------------------------------------------------------------------------------- the verdict


def _rows(n: int, diff, *, start: str = "2018-01-01", gap: int = 20, names=("SOXL", "TQQQ")) -> list[dict]:
    out = []
    d = date.fromisoformat(start)
    for i in range(n):
        entry = (d + timedelta(days=gap * i)).isoformat()
        x = diff(i) if callable(diff) else diff
        out.append(
            {
                "name": names[i % len(names)],
                "entry": entry,
                "date": entry,
                "diff": x,
                "diff_stress": x - 1.0,
                "diff_mid": x + 1.0,
                "hospital_days": 40,
                "hold_days": 40,
                "hold_capital_days": 1.0,
                "capital_days": 1.0,
                "premium": 0.0,
                "units": 1,
                "waits": 0,
                "fallbacks": 0,
                "stale": 0,
                "below_cost": False,
                "rebound": False,
                "how": "assigned",
                "net": x,
                "hold_net": 0.0,
                "kit": {"date": entry, "net": x, "bp": 10_000.0, "days": 0, "rate": 0.02},
            }
        )
    return out


def _noisy(i: int) -> float:
    return 50.0 + (i % 7 - 3) * 10.0  # mean 50, a spread that keeps t well over 2


def _arm(design_n: int = 40, hold_n: int = 12, design=_noisy, holdout=_noisy):
    return _rows(design_n, design, start="2016-06-01", gap=75) + _rows(
        hold_n, holdout, start="2025-02-01", gap=20
    )


def test_an_arm_that_beats_the_baseline_everywhere_passes_when_the_holdout_agrees():
    block = m.judge(m.RULE, {"repair-atm": _arm(), "repair-cost": _arm()})
    assert block["verdict"] == {"repair-atm": "pass", "repair-cost": "pass"}
    assert [p["verdict"] for p in block["arms"]["repair-atm"]["parts"]] == ["pass", "pass"]
    assert block["arms"]["repair-atm"]["parts"][0]["bars"]["tail"]["verdict"] == "not applicable"


def test_a_design_that_fails_leaves_the_holdout_unread():
    block = m.judge(
        m.RULE, {"repair-atm": _arm(design=lambda i: -50.0 + (i % 7 - 3) * 10.0), "repair-cost": _arm()}
    )
    arm = block["arms"]["repair-atm"]
    assert arm["verdict"] == "fail"
    assert arm["parts"][1]["verdict"] == kit_rule.NOT_READ


def test_a_passing_design_with_a_losing_holdout_fails():
    block = m.judge(m.RULE, {"repair-atm": _arm(holdout=lambda i: -20.0), "repair-cost": _arm()})
    assert block["verdict"]["repair-atm"] == "fail"


def test_too_few_hospital_lots_is_no_verdict_and_too_few_in_the_holdout_is_no_verdict():
    assert (
        m.judge(m.RULE, {"repair-atm": _arm(design_n=29), "repair-cost": _arm()})["verdict"]["repair-atm"]
        == "no verdict"
    )
    assert (
        m.judge(m.RULE, {"repair-atm": _arm(hold_n=9), "repair-cost": _arm()})["verdict"]["repair-atm"]
        == "no verdict"
    )


def test_a_design_with_one_losing_half_fails_though_its_mean_and_t_pass():
    # early lots lose a little, late lots win a lot: the mean and t clear the bar, the first half is negative
    def skewed(i):
        return -5.0 + (i % 7 - 3) * 1.0 if i < 21 else 120.0 + (i % 7 - 3) * 10.0

    block = m.judge(m.RULE, {"repair-atm": _arm(design=skewed), "repair-cost": _arm()})
    arm = block["arms"]["repair-atm"]
    assert arm["verdict"] == "fail" and arm["parts"][0]["extra"]["halves"] is False
    assert arm["parts"][0]["bars"]["capital"]["verdict"] == "pass"


def test_a_design_that_loses_on_one_name_fails():
    rows = _arm()
    for r in rows:
        if r["name"] == "TQQQ" and r["entry"] < "2025-01-01":
            r["diff"], r["kit"]["net"], r["diff_stress"] = -2.0, -2.0, -3.0
    block = m.judge(m.RULE, {"repair-atm": rows, "repair-cost": _arm()})
    assert block["verdict"]["repair-atm"] == "fail"
    assert block["arms"]["repair-atm"]["parts"][0]["extra"]["names"] is False


def test_an_arm_that_loses_under_the_stress_fails():
    rows = _arm()
    for r in rows:
        r["diff_stress"] = -1.0
    block = m.judge(m.RULE, {"repair-atm": rows, "repair-cost": _arm()})
    assert block["arms"]["repair-atm"]["parts"][0]["extra"]["stress"] is False
    assert block["verdict"]["repair-atm"] == "fail"


def _borderline(t: float, n: int = 40) -> list[dict]:
    """n design lots whose paired differences have exactly one-sided t `t`: the mean is t / sqrt(n) of a unit
    spread, the values alternate either side of it, the names in pairs so neither is negative."""
    spread = (n - 1) / n
    mean = t / n**0.5
    rows = _rows(
        n, lambda i: mean + (spread**0.5 if i % 2 else -(spread**0.5)), start="2016-06-01", gap=75,
        names=("SOXL", "SOXL", "TQQQ", "TQQQ"),
    )  # fmt: skip
    for r in rows:
        r["diff_stress"] = r["diff"]
    return rows + _rows(12, lambda i: 5.0 + i % 3, start="2025-02-01", gap=20)


def test_holm_across_the_judged_arms_stops_two_arms_that_clear_the_t_bar_only_just():
    # t = 2.005 on 40 lots: a one-sided p near 0.026, over the kit's bar of 2 and Holm's 0.025 for two arms
    block = m.judge(m.RULE, {"repair-atm": _borderline(2.005), "repair-cost": _borderline(2.005)})
    part = block["arms"]["repair-atm"]["parts"][0]
    assert part["bars"]["capital"]["verdict"] == "pass"
    assert part["extra"] == {"holm": False, "halves": True, "names": True, "stress": True}
    assert block["verdict"] == {"repair-atm": "fail", "repair-cost": "fail"}
    # the same arms a little stronger clear Holm, which shows the check is what failed them
    firm = m.judge(m.RULE, {"repair-atm": _borderline(2.3), "repair-cost": _borderline(2.3)})
    assert firm["verdict"] == {"repair-atm": "pass", "repair-cost": "pass"}


def test_the_capital_bar_charges_the_bill_on_the_extra_days_the_arm_holds_the_lot():
    rows = _arm()
    for r in rows:
        r["kit"]["days"] = 365 * 10  # ten extra years of a 2% bill on 10,000 dollars: 2,000 dollars a lot
    block = m.judge(m.RULE, {"repair-atm": rows, "repair-cost": _arm()})
    assert block["verdict"]["repair-atm"] == "fail"


def test_the_block_is_json_and_names_every_judged_arm():
    block = m.judge(m.RULE, {"repair-atm": _arm(), "repair-cost": _arm()})
    assert set(json.loads(json.dumps(block))["verdict"]) == set(m.JUDGED)
    assert block["arms"]["repair-atm"]["n"] == 52
