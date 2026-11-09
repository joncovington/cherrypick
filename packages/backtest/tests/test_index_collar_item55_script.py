"""Hypothesis 55's study (packages/backtest/scripts/index_collar_item55.py): the entry session, the three
hedges and the collar's strike, the sizing, the fills and the declared decision rules. Invented chains only,
no store.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("index_collar_item55", SCRIPTS / "index_collar_item55.py")
s55 = importlib.util.module_from_spec(_spec)
sys.modules["index_collar_item55"] = s55
_spec.loader.exec_module(s55)

SPOT = 100.0


def chain_at(spot=SPOT, put_richness=1.0, spread=0.10):
    """Strikes 70-130. Time value is a bell around the spot (richer for puts by `put_richness`),
    intrinsic on top; |delta| falls 0.03 a strike away from the money. Puts carry negative deltas."""
    c = {}
    for k in range(70, 131):
        bell = 3.2 * math.exp(-(((k - spot) / 7.0) ** 2))
        for right in ("C", "P"):
            tv = bell * (put_richness if right == "P" else 1.0)
            intr = max(spot - k, 0.0) if right == "C" else max(k - spot, 0.0)
            m = intr + max(tv, 0.03)
            away = (k - spot) if right == "C" else (spot - k)
            d = min(max(0.5 - 0.03 * away, 0.02), 0.98)
            c[(float(k), right)] = (m - spread / 2, m + spread / 2, d if right == "C" else -d)
    return c


def test_the_entry_session_is_the_nearest_at_or_beyond_the_target():
    exp = date(2027, 3, 19)
    sessions = [date(2027, 1, 29), date(2027, 2, 2), date(2027, 2, 8)]
    assert s55.entry_session(exp, 45, sessions) == date(2027, 2, 2)  # 45 days, the nearest to 45
    assert s55.entry_session(exp, 45, [date(2027, 2, 8)]) is None  # 39 days is under the target
    assert s55.entry_session(exp, 45, [date(2027, 1, 29)]) is None  # 49 days is past 45 + 3


def test_the_monthly_moves_off_a_holiday_friday():
    assert s55.monthly(2025, 4) == date(2025, 4, 17)  # Good Friday
    assert s55.monthly(2027, 3) == date(2027, 3, 19)


def test_a_credit_call_spread_and_a_debit_put_spread_are_built_as_hedge_py_builds_them():
    c = chain_at()
    # |delta| 0.5 - 0.03 away: the call nearest 0.30 is the 107 (0.29), its wing five above.
    assert s55.credit_legs(c) == [(-1, "C", 107.0), (1, "C", 112.0)]
    # The put nearest 0.40 is the 97 (0.41), sold against the 92.
    assert s55.debit_legs(c) == [(1, "P", 97.0), (-1, "P", 92.0)]


def test_a_strike_without_a_two_sided_quote_is_not_picked():
    c = chain_at()
    c[(107.0, "C")] = (None, 0.30, 0.29)
    assert s55.nearest_delta(c, "C", 0.30) == 106.0
    for k in range(70, 131):
        c[(float(k), "C")] = (0.0, 0.0, 0.2)
    assert s55.credit_legs(c) is None and s55.build(c) == "C"


def _brute_nets(c):
    """Every call credit spread the collar could sell with the debit half, and the net it makes."""
    paid = s55.net_credit(c, s55.debit_legs(c))
    out = {}
    for (k, r), q in c.items():
        if r == "C" and s55.MIN_SHORT_DELTA <= abs(q[2]) < 0.5:
            w = s55.wing(c, "C", k, s55.WIDTH, 1.0)
            if w is None:
                continue
            out[k] = paid + s55.net_credit(c, [(-1, "C", k), (1, "C", w)])
    return out


def test_the_collar_sells_the_call_that_brings_the_net_nearest_zero():
    c = chain_at()
    legs = s55.collar_legs(c)
    nets = _brute_nets(c)
    chosen = legs[2][2]
    assert legs[:2] == s55.debit_legs(c) and legs[2][0] == -1 and legs[3] == (1, "C", chosen + 5.0)
    assert abs(nets[chosen]) == min(abs(v) for v in nets.values())
    assert abs(s55.net_credit(c, legs)) <= s55.MAX_NET
    # A different surface moves the strike: cheaper puts need less funding, so the call is sold
    # further out; and puts too rich for any call spread to fund leave no collar at all.
    cheaper = s55.collar_legs(chain_at(put_richness=0.7))
    assert cheaper is not None and cheaper[2][2] > chosen
    assert s55.collar_legs(chain_at(put_richness=1.15)) is None


def test_a_collar_further_than_the_allowance_from_zero_is_not_built():
    c = chain_at()
    # Every call is worth the minimum: no credit spread can fund the put spread.
    for k in range(70, 131):
        b, a, d = c[(float(k), "C")]
        intr = max(SPOT - k, 0.0)
        c[(float(k), "C")] = (intr + 0.0, intr + 0.06, d)
    assert abs(min(abs(v) for v in _brute_nets(c).values())) > s55.MAX_NET
    assert s55.collar_legs(c) is None
    assert s55.build(c) == "K"


def test_the_collar_never_sells_a_call_that_is_not_out_of_the_money_by_delta():
    legs = s55.collar_legs(chain_at())
    assert 0 < abs(chain_at()[(legs[2][2], "C")][2]) < 0.5


def test_each_hedge_is_scaled_to_cut_twenty_shares_of_delta():
    c = chain_at()
    for arm, legs in s55.build(c).items():
        scale = s55.hedge_scale(c, legs)
        assert scale is not None and scale * s55.unit_delta(c, legs) == pytest.approx(-s55.HEDGE_CUT), arm
    # A structure that adds delta cuts nothing.
    assert s55.hedge_scale(c, [(1, "C", 100.0), (-1, "C", 105.0)]) is None


def test_the_signs_of_the_stored_deltas_do_not_matter():
    c = chain_at()
    flipped = {k: (b, a, (abs(d) if k[1] == "P" else -abs(d))) for k, (b, a, d) in c.items()}
    legs = s55.debit_legs(c)
    assert s55.unit_delta(c, legs) == pytest.approx(s55.unit_delta(flipped, legs))


def test_a_forced_close_prices_an_unquoted_leg_adversely():
    entry = {(97.0, "P"): (1.0, 1.2, -0.4), (92.0, "P"): (0.4, 0.5, -0.2)}
    legs = [(1, "P", 97.0), (-1, "P", 92.0)]
    q = s55.forced_quotes({}, legs, entry, 90.0)
    assert q[0]["filled"] and abs(q[0]["bid"] - (7.0 - 0.2)) < 1e-9  # long: intrinsic 7 less its spread
    assert q[1] == {"bid": 2.1, "ask": 2.1, "filled": True}  # short: intrinsic 2 plus its spread 0.1


def test_the_hedge_net_takes_slippage_and_fees_both_ways_and_scales_with_the_units():
    legs = [(1, "P", 97.0), (-1, "P", 92.0)]
    entry = [{"bid": 2.4, "ask": 2.6}, {"bid": 1.5, "ask": 1.7}]
    exit_ = [{"bid": 5.9, "ask": 6.1}, {"bid": 1.9, "ask": 2.1}]
    n = s55.hedge_net(entry, exit_, legs, SPOT, 1.0)
    assert n["gross"] == pytest.approx(100.0 * ((6.0 - 2.5) - (2.0 - 1.6)))
    assert n["working"]["net"] < n["gross"]
    assert n["stress"]["net"] < n["working"]["net"]
    assert n["working"]["bp"] == pytest.approx(1e4 * n["working"]["net"] / (100.0 * SPOT))
    two = s55.hedge_net(entry, exit_, legs, SPOT, 2.0)
    assert two["working"]["net"] == pytest.approx(2 * n["working"]["net"])
    assert two["fees"] == pytest.approx(2 * n["fees"])


def test_the_tail_is_the_worst_fifth_and_at_least_one():
    assert s55.tail([5, -10, 3]) == -10
    assert s55.tail(list(range(-20, 20))) == pytest.approx(-16.5)  # 40 values: the worst 8


def test_the_ratio_is_the_mean_over_the_tail_and_the_tail_is_floored():
    values = [10.0, 20.0, -30.0, 40.0, -50.0]  # worst fifth is -50; mean -> (10+20-30+40-50)/5 = -2
    assert s55.ratio(values) == pytest.approx(-2.0 / 50.0)
    assert s55.ratio([4.0, 6.0]) == pytest.approx(5.0 / s55.TAIL_FLOOR_BP)  # no loss: the floor
    assert s55.ratio([]) is None


def test_the_bootstrap_is_seeded_and_tells_a_real_edge_from_none():
    base = [(-1.0) ** i * (5 + i % 7) for i in range(60)]
    better = [x + 3.0 for x in base]
    p = s55.ratio_diff_p(better, base, resamples=600)
    assert p == s55.ratio_diff_p(better, base, resamples=600)  # the same twice
    assert p < 0.05
    assert s55.ratio_diff_p(base, base, resamples=600) > 0.9
    assert s55.ratio_diff_p(base, better, resamples=600) > 0.9
    assert s55.ratio_diff_p([1.0], [0.0]) == 1.0


def _cycle(n, k, stress_k=None, c=None, d=None):
    arm = lambda v, s=None: {"bp": v, "stress_bp": v if s is None else s}  # noqa: E731
    return {
        "arms": {
            "N": arm(n),
            "K": arm(k, stress_k),
            "C": arm(n if c is None else c),
            "D": arm(n if d is None else d),
        }
    }


def _book(shift, n=48):
    """Cycles on which K beats N by `shift` basis points, N mixed."""
    base = [8.0, -12.0, 3.0, -6.0, 15.0, -2.0]
    return [_cycle(base[i % 6] + (i % 4), base[i % 6] + (i % 4) + shift) for i in range(n)]


def test_the_design_rule_needs_holm_the_stress_the_best_five_and_the_tail():
    cycles = _book(2.5)
    cmp = s55.compare(cycles, "K", "N", resamples=400)
    ok = s55.design_decision(cmp, True, len(cycles))
    assert ok["decision"] == "pass", ok
    assert s55.design_decision(cmp, False, len(cycles))["checks"]["ratio_holm"] is False
    assert s55.design_decision(cmp, True, 10)["decision"] == "no verdict"
    # Only at the working costs: the stress takes it back.
    stressed = [
        _cycle(c["arms"]["N"]["bp"], c["arms"]["K"]["bp"], stress_k=c["arms"]["N"]["bp"] - 1) for c in cycles
    ]
    checks = s55.design_decision(s55.compare(stressed, "K", "N", resamples=400), True, len(stressed))[
        "checks"
    ]
    assert checks["stress_ratio_positive"] is False
    # Five cycles carry it.
    carried = [_cycle(1.0 + i % 3, 1.0 + i % 3 + (60.0 if i < 5 else 0.0)) for i in range(48)]
    carried_checks = s55.design_decision(s55.compare(carried, "K", "N", resamples=400), True, 48)["checks"]
    assert carried_checks["best_five_removed_positive"] is False
    # A worse tail fails even with a better mean.
    worse = [_cycle(1.0, 4.0) for _ in range(46)] + [_cycle(-2.0, -40.0) for _ in range(2)]
    worse_checks = s55.design_decision(s55.compare(worse, "K", "N", resamples=400), True, 48)["checks"]
    assert worse_checks["tail_no_worse"] is False


def test_the_paired_reading_also_reports_what_the_hedge_gives_up_and_what_it_saves():
    cmp = s55.compare(_book(2.5), "K", "N", resamples=200)
    assert cmp["mean_diff"] == pytest.approx(2.5)
    assert cmp["mean_diff_when_book_lost"] == pytest.approx(2.5)
    assert cmp["cycles"] == 48


def test_the_holdout_only_judges_a_design_pass_and_wants_p_under_five_percent():
    good = s55.compare(_book(4.0, 60), "K", "N", resamples=400)
    assert s55.holdout_decision({"decision": "fail"}, good, 60)["decision"] == "not judged"
    assert s55.holdout_decision({"decision": "pass"}, good, 60)["decision"] == "pass"
    assert s55.holdout_decision({"decision": "pass"}, good, 10)["decision"] == "no verdict"
    assert s55.holdout_decision({"decision": "pass"}, {**good, "p": 0.2}, 60)["decision"] == "fail"
    assert s55.holdout_decision({"decision": "pass"}, {**good, "ratio_diff": -0.1}, 60)["decision"] == "fail"
    assert (
        s55.holdout_decision({"decision": "pass"}, {**good, "tail_x": -9.0, "tail_ref": -1.0}, 60)["decision"]
        == "fail"
    )


def test_the_collar_label_lifts_only_when_it_beats_none_and_both_verticals():
    everything = ["K|N", "K|C", "K|D", "C|N"]
    assert s55.collar_label_lifts(everything) is True
    for missing in ("K|N", "K|C", "K|D"):
        assert s55.collar_label_lifts([t for t in everything if t != missing]) is False
    assert s55.collar_label_lifts(["C|N", "D|N"]) is False


def test_the_family_is_five_tests_and_the_windows_split_at_2022():
    assert len(s55.TESTS) == 5 and ("K", "N") in s55.TESTS and ("K", "D") in s55.TESTS
    assert s55.window_of(date(2021, 12, 31)) == "design"
    assert s55.window_of(date(2022, 1, 3)) == "holdout"
    assert s55.window_of(date(2016, 12, 30)) is None


def _sessions(entry_spot, exit_spot, expiry=date(2027, 3, 19)):
    return {
        date(2027, 2, 2): (entry_spot, chain_at(entry_spot)),
        date(2027, 3, 8): (entry_spot, chain_at(entry_spot)),
        date(2027, 3, 17): (exit_spot, chain_at(exit_spot)),
    }


def test_a_cycle_runs_every_arm_on_the_same_book_move():
    expiry = date(2027, 3, 19)
    t, why = s55.simulate(_sessions(100.0, 90.0), expiry)
    assert why is None and t["entry"] == "2027-02-02" and t["exit"] == "2027-03-17"
    # The book alone: 100 shares, down ten, in basis points of its notional.
    assert t["arms"]["N"]["bp"] == pytest.approx(-1000.0)
    # In a fall every hedge pays its legs, so each beats the bare book; the call spread (C) least.
    assert all(t["arms"][a]["bp"] > t["arms"]["N"]["bp"] for a in s55.HEDGES)
    assert t["arms"]["D"]["bp"] > t["arms"]["C"]["bp"]
    # In a rise the puts expire and the collar's call spread costs it what the bare book gains.
    up, _ = s55.simulate(_sessions(100.0, 110.0), expiry)
    assert up["arms"]["K"]["bp"] < up["arms"]["N"]["bp"] and up["arms"]["D"]["bp"] < up["arms"]["N"]["bp"]
    assert abs(t["collar_net"]) <= s55.MAX_NET and t["collar_call_delta"] < 0.5


def test_a_cycle_is_dropped_with_the_reason():
    expiry = date(2027, 3, 19)
    assert s55.simulate({date(2027, 3, 8): (100.0, chain_at())}, expiry)[1] == "no_entry_session"
    only_entry = {date(2027, 2, 2): (100.0, chain_at())}
    assert s55.simulate(only_entry, expiry)[1] == "no_exit_mark"
    assert s55.simulate({date(2027, 2, 2): (None, chain_at())}, expiry)[1] == "no_spot"
    assert s55.simulate({date(2027, 2, 2): (100.0, {})}, expiry)[1] == "no_hedge_C"
    no_exit_spot = _sessions(100.0, 90.0)
    no_exit_spot[date(2027, 3, 17)] = (None, chain_at(90.0))
    assert s55.simulate(no_exit_spot, expiry)[1] == "no_exit_spot"


def test_a_study_that_judged_nothing_is_no_verdict_not_failed():
    nothing = {"a": {"decision": "no verdict"}, "b": {"decision": "no verdict"}}
    assert s55.overall_verdict(nothing, []) == "no verdict"
    judged = {"a": {"decision": "no verdict"}, "b": {"decision": "fail"}}
    assert s55.overall_verdict(judged, []) == "failed"
    assert s55.overall_verdict(judged, ["b"]) == "passed"
