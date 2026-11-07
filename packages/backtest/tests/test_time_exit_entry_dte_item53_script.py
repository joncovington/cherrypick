"""Hypothesis 53's study (scripts/time_exit_entry_dte_item53.py): the entry session, the strikes, the
exits of each arm, the fills and the declared decision rules. Invented chains only, no store.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location(
    "time_exit_entry_dte_item53", SCRIPTS / "time_exit_entry_dte_item53.py"
)
s53 = importlib.util.module_from_spec(_spec)
sys.modules["time_exit_entry_dte_item53"] = s53
_spec.loader.exec_module(s53)

SPOT = 100.0


def chain():
    """Calls 101-110 and puts 90-99, |delta| falling a step a strike away from spot."""
    c = {}
    for i, k in enumerate(range(101, 111)):
        c[(float(k), "C")] = (1.0 - 0.09 * i, 1.1 - 0.09 * i, 0.40 - 0.03 * i)
    for i, k in enumerate(range(99, 89, -1)):
        c[(float(k), "P")] = (1.0 - 0.09 * i, 1.1 - 0.09 * i, -(0.40 - 0.03 * i))
    return c


def test_the_entry_session_is_the_nearest_at_or_beyond_the_target():
    exp = date(2027, 3, 19)
    sessions = [date(2027, 3, 1), date(2027, 3, 3), date(2027, 3, 8)]
    assert s53.entry_session(exp, 14, sessions) == date(2027, 3, 3)  # 16 days, inside 14..17
    assert s53.entry_session(exp, 21, sessions) is None  # 18 and 16 are under 21, nothing in 21..24
    assert s53.entry_session(exp, 7, sessions) is None  # 11 days is past 7 + 3


def test_the_monthly_moves_off_a_holiday_friday():
    assert s53.monthly(2025, 4) == date(2025, 4, 17)  # Good Friday
    assert s53.monthly(2027, 3) == date(2027, 3, 19)


def test_the_scaled_exit_keeps_the_21_of_45_share():
    assert [s53.scaled_exit_dte(d) for d in (7, 14, 21, 28)] == [3, 7, 10, 13]
    # B at 7 DTE is the 3-DTE close: not a test of its own.
    family = s53.tests_family()
    assert ("strangle", 7, "B") not in family and ("condor", 14, "B") in family
    assert len(family) == 22


def test_the_shorts_are_the_out_of_the_money_strikes_nearest_16_delta_and_the_wings_one_percent_out():
    legs = s53.build(chain(), SPOT, "condor")
    # |delta| 0.40 - 0.03 i: 0.16 at i = 8, so the 109 call and the 91 put; wings at 110 and 90.
    assert legs == [(-1, "P", 91.0), (-1, "C", 109.0), (1, "P", 90.0), (1, "C", 110.0)]
    assert s53.build(chain(), SPOT, "strangle") == legs[:2]


def test_a_leg_without_a_two_sided_quote_is_not_picked():
    c = chain()
    c[(109.0, "C")] = (None, 0.30, 0.16)
    assert s53.pick_short(c, "C", SPOT) in (108.0, 110.0)
    for k in range(101, 111):
        c[(float(k), "C")] = (0.0, 0.0, 0.2)
    assert s53.build(c, SPOT, "strangle") is None


def test_the_stop_is_one_credit_or_half_the_max_loss_when_smaller():
    assert s53.stop_level(1.0, None) == 1.0
    assert s53.stop_level(1.0, 4.0) == 1.0
    assert s53.stop_level(1.0, 1.5) == 1.0  # the credit stop is the smaller
    assert s53.stop_level(1.0, 0.8) == 0.4  # a narrow condor: half its max loss


def test_each_arm_exits_on_its_own_rule():
    credit = 1.0
    # days left, cost to close: rises to a stop, then falls to the target, then the close.
    path = [(20, 1.2), (18, 2.1), (16, 0.45), (12, 0.40), (9, 0.30), (3, 0.10)]
    assert s53.exit_for("A", 21, credit, None, path) == (1, "stop")
    assert s53.exit_for("C", 21, credit, None, path) == (2, "take_profit")
    assert s53.exit_for("D", 21, credit, None, path) == (5, "close_dte")
    calm = [(20, 0.9), (15, 0.8), (10, 0.7), (6, 0.6), (3, 0.2)]
    assert s53.exit_for("B", 21, credit, None, calm) == (2, "scaled_exit")  # 10 days left = round(21 x 21/45)
    assert s53.exit_for("A", 21, credit, None, calm) == (4, "close_dte")


def test_an_unquoted_mark_holds_every_trigger_but_the_close():
    path = [(20, None), (10, None), (3, None)]
    assert s53.exit_for("A", 21, 1.0, None, path) == (2, "close_dte")
    assert s53.exit_for("A", 21, 1.0, None, [(20, None)]) is None  # the store ends first


def test_a_forced_close_prices_an_unquoted_leg_adversely():
    entry = {(91.0, "P"): (0.5, 0.7, -0.16), (90.0, "P"): (0.4, 0.5, -0.12)}
    legs = [(-1, "P", 91.0), (1, "P", 90.0)]
    q = s53.forced_quotes({}, legs, entry, 89.0)
    assert q[0] == {"bid": 2.2, "ask": 2.2, "filled": True}  # short: intrinsic 2 + spread 0.2
    assert abs(q[1]["bid"] - 0.9) < 1e-9  # long: intrinsic 1 - spread 0.1


def test_the_net_takes_slippage_and_fees_both_ways_and_the_stress_costs_more():
    legs = [(-1, "P", 91.0), (-1, "C", 109.0)]
    entry = [{"bid": 1.0, "ask": 1.2}, {"bid": 1.0, "ask": 1.2}]
    exit_ = [{"bid": 0.4, "ask": 0.6}, {"bid": 0.4, "ask": 0.6}]
    n = s53.net(entry, exit_, legs, SPOT)
    assert abs(n["credit"] - 220.0) < 1e-9 and abs(n["exit_cost"] - 100.0) < 1e-9
    assert n["working"]["net"] < 120.0
    assert n["stress"]["net"] < n["working"]["net"]
    assert abs(n["working"]["bp"] - 1e4 * n["working"]["net"] / 1e4) < 1e-9


def test_the_tail_is_the_worst_five_percent_and_at_least_one():
    assert s53.tail([5, -10, 3]) == -10
    assert s53.tail(list(range(-20, 20))) == -19.5  # 40 values: the worst 2


def _trade(a, c, stress_a=None, stress_c=None):
    return {
        "arms": {
            "A": {"bp": a, "stress_bp": stress_a if stress_a is not None else a},
            "C": {"bp": c, "stress_bp": stress_c if stress_c is not None else c},
        }
    }


def test_the_design_rule_needs_holm_the_stress_the_best_five_and_the_tail():
    trades = [_trade(a, a + 2.0 + (i % 3)) for i, a in enumerate([1.0, -3.0, 2.0, -1.0] * 12)]
    cmp = s53.compare(trades, "C")
    ok = s53.design_decision(cmp, True, len(trades))
    assert ok["decision"] == "pass"
    assert s53.design_decision(cmp, False, len(trades))["checks"]["diff_holm"] is False
    assert s53.design_decision(cmp, True, 10)["decision"] == "no verdict"
    # A challenger that earns more on average but has the worse tail fails.
    worse = [_trade(1.0, 4.0) for _ in range(46)] + [_trade(-2.0, -40.0) for _ in range(2)]
    assert s53.design_decision(s53.compare(worse, "C"), True, 48)["checks"]["tail_no_worse"] is False


def test_the_holdout_only_judges_a_design_pass():
    good = s53.compare([_trade(0.0, 1.0) for _ in range(45)], "C")
    assert s53.holdout_decision({"decision": "fail"}, good, 45)["decision"] == "not judged"
    assert s53.holdout_decision({"decision": "pass"}, good, 45)["decision"] == "pass"
    bad = s53.compare([_trade(1.0, 0.0) for _ in range(45)], "C")
    assert s53.holdout_decision({"decision": "pass"}, bad, 45)["decision"] == "fail"


def test_the_windows_split_at_2022():
    assert s53.window_of(date(2021, 12, 31)) == "design"
    assert s53.window_of(date(2022, 1, 3)) == "holdout"
    assert s53.window_of(date(2016, 12, 30)) is None


def test_a_study_that_judged_nothing_is_no_verdict_not_failed():
    nothing = {"a": {"decision": "no verdict"}, "b": {"decision": "no verdict"}}
    assert s53.overall_verdict(nothing, []) == "no verdict"
    judged = {"a": {"decision": "no verdict"}, "b": {"decision": "fail"}}
    assert s53.overall_verdict(judged, []) == "failed"
    assert s53.overall_verdict(judged, ["b"]) == "passed"
