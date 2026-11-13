"""Hypothesis 73's study (packages/backtest/scripts/meic_1400_stack_item73.py): the arms it declares, the
increments it judges, its verdict and the reads beside it. Invented sessions only, no store.

Each bar is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import random
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location(
    "meic_1400_stack_item73", SCRIPTS / "meic_1400_stack_item73.py"
)
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)

START, END = "2022-01-03", "2023-12-29"
PM200 = {
    "_note": "x",
    "enabled": False,
    "symbols": ["SPX"],
    "short_delta_target": 0.16,
    "paper_entry_window_start": "12:00",
    "entry_window_end": "14:30",
    "min_credit_pct_of_width": 0.15,
    "per_side_stop_management": True,
    "stop_trigger_ratio": 2.0,
    "overlap_scope": "none",
    "stagger_entries": True,
    "min_minutes_between_entries": 30,
    "daily_ic_trade_target": 999,
    "wing_width_points": 10,
}
REGISTRY = {"profiles": {"pm-200": PM200, "control": {"enabled": True}}}


def days(n):
    d0 = date.fromisoformat(START)
    span = (date.fromisoformat(END) - d0).days
    return [(d0 + timedelta(days=i * span // max(n - 1, 1))).isoformat() for i in range(n)]


def sessions(nets, fees=1.0, trades=1):
    return {d: {"net": x, "fees": fees, "trades": trades} for d, x in zip(days(len(nets)), nets, strict=True)}


def noisy(mean, n=150, sd=10.0, seed=1):
    rng = random.Random(seed)
    return [mean + rng.gauss(0, sd) for _ in range(n)]


def stacked(base, extra, fees=1.0):
    """The larger arm: the smaller arm's sessions plus one extra condor's net each."""
    return {d: {"net": v["net"] + x, "fees": v["fees"] + fees, "trades": v["trades"] + 1}
            for (d, v), x in zip(sorted(base.items()), extra, strict=True)}  # fmt: skip


# ── the arms ─────────────────────────────────────────────────────────────────


def test_the_arms_are_live_rule_at_one_two_and_three_a_session_ten_minutes_apart():
    profiles = st.study_registry(REGISTRY)["profiles"]
    for name, n in {"n1": 1, "n2": 2, "n3": 3}.items():
        p = profiles[name]
        assert p["daily_ic_trade_target"] == n
        assert p["enabled"] is True and p["stagger_entries"] is True
        assert (p["paper_entry_window_start"], p["entry_window_end"]) == ("14:00", "14:30")
        assert p["min_minutes_between_entries"] == 10
        assert p["stop_resting"] is True and p["stop_resting_limit_frac"] == 0.1
        assert p["stop_trigger_ratio"] == 2.0 and p["wing_width_points"] == 10
        assert not any(k.startswith("_") for k in p)
    # Nothing but the count differs between them.
    a, b, c = (dict(profiles[n]) for n in ("n1", "n2", "n3"))
    for p in (a, b, c):
        p.pop("daily_ic_trade_target")
    assert a == b == c
    assert profiles["pm-200"] is PM200 and profiles["control"] == {"enabled": True}


def test_the_registry_refuses_a_base_arm_that_changed_under_it():
    with pytest.raises(SystemExit, match="2.0x"):
        st.study_registry({"profiles": {"pm-200": {**PM200, "stop_trigger_ratio": 1.5}}})
    with pytest.raises(SystemExit, match="2.0x"):
        st.study_registry({"profiles": {"pm-200": {**PM200, "per_side_stop_management": False}}})
    with pytest.raises(SystemExit, match="overlap"):
        st.study_registry({"profiles": {"pm-200": {**PM200, "overlap_scope": "sign"}}})
    with pytest.raises(SystemExit, match="no 'pm-200'"):
        st.study_registry({"profiles": {}})


# ── the increments ───────────────────────────────────────────────────────────


def test_an_increment_is_the_extra_condor_session_by_session():
    small = sessions([10.0, -5.0, 3.0])
    large = stacked(small, [4.0, -2.0, 0.0])
    large[days(3)[2]]["trades"] = 1  # no extra condor that session
    inc = st.increment(large, small, START, END)
    assert inc["n"] == 3 and inc["sessions_with_extra"] == 2
    assert inc["mean"] == pytest.approx((4 - 2 + 0) / 3, abs=0.01)
    assert inc["worst_session"] == pytest.approx(-2.0)


def test_a_session_only_one_arm_ran_counts_at_zero_for_the_other():
    small = sessions([10.0, 10.0])
    large = {days(2)[0]: {"net": 15.0, "fees": 2.0, "trades": 2}}
    inc = st.increment(large, small, START, END)
    assert inc["n"] == 2 and inc["mean"] == pytest.approx((5.0 - 10.0) / 2)


def test_nested_share_reads_whether_the_larger_arm_starts_with_the_smaller_arms_entries():
    e1 = {"entry_time": "t1", "put_strike": 1, "call_strike": 2, "net_credit": 1.0}
    e2 = {"entry_time": "t2", "put_strike": 1, "call_strike": 2, "net_credit": 0.9}
    other = {**e1, "put_strike": 0}
    assert st.nested_share({"d1": [e1], "d2": [e1]}, {"d1": [e1, e2], "d2": [e1]}) == 1.0
    assert st.nested_share({"d1": [e1], "d2": [e1]}, {"d1": [other, e2], "d2": [e1]}) == 0.5
    assert st.nested_share({}, {}) is None


def test_session_bp_sums_every_condor_held_that_session():
    rows = [{"wing_width": 10, "net_credit": 1.5, "quantity": 1}, {"wing_width": 10, "net_credit": 2.0}]
    assert st.session_bp(rows) == pytest.approx(850 + 800)


# ── the verdict ──────────────────────────────────────────────────────────────


def _case(second_nets, third_nets, fees=1.0, dd=(0.01, 0.01, 0.01)):
    incs = {
        "second": st.increment(
            stacked(sessions([0.0] * len(second_nets)), second_nets, fees),
            sessions([0.0] * len(second_nets)),
            START,
            END,
        ),  # fmt: skip
        "third": st.increment(
            stacked(sessions([0.0] * len(third_nets)), third_nets, fees),
            sessions([0.0] * len(third_nets)),
            START,
            END,
        ),  # fmt: skip
    }
    arms = {n: {"drawdown": {"mean_over_dd": x}} for n, x in zip(("n1", "n2", "n3"), dd, strict=True)}
    st.judge(incs, arms)
    return incs


def test_a_strong_extra_condor_passes_every_check():
    incs = _case(noisy(5.0), noisy(5.0, seed=2))
    assert incs["second"]["verdict"] == "pass" and all(incs["second"]["checks"].values())
    assert st.proposed_count(incs) == 3


def test_a_flat_extra_condor_fails_the_mean_and_the_t():
    incs = _case(noisy(-1.0), noisy(5.0, seed=2))
    assert incs["second"]["verdict"] == "fail"
    assert not incs["second"]["checks"]["mean_positive"] and not incs["second"]["checks"]["t_above_bar"]
    # A third that passes counts for nothing when the second did not.
    assert incs["third"]["verdict"] == "pass" and st.proposed_count(incs) == 1


def test_a_positive_mean_short_of_the_t_bar_fails():
    incs = _case(noisy(0.5, sd=30.0), noisy(5.0, seed=2))
    c = incs["second"]["checks"]
    assert c["mean_positive"] and not c["t_above_bar"] and incs["second"]["verdict"] == "fail"


def test_holm_across_two_could_never_bind_so_it_is_not_a_check():
    # The declaration drops Holm because t above 2 on 100+ sessions is already under its strictest bar.
    assert st.p_one(2.0 + 1e-9, st.MIN_SESSIONS) < 0.05 / 2
    assert "holm" not in _case(noisy(5.0), noisy(5.0, seed=2))["second"]["checks"]


def test_an_increment_carried_by_one_half_fails():
    nets = noisy(0.0, sd=1.0)
    nets = [x + (12.0 if i < 75 else -2.0) for i, x in enumerate(nets)]
    incs = _case(nets, noisy(5.0, seed=2))
    c = incs["second"]["checks"]
    assert c["mean_positive"] and c["t_above_bar"] and not c["both_halves_positive"]


def test_an_increment_that_dies_at_one_and_a_half_times_fees_fails():
    incs = _case(noisy(5.0, sd=2.0), noisy(5.0, seed=2), fees=12.0)
    c = incs["second"]["checks"]
    assert c["mean_positive"] and c["t_above_bar"] and not c["survives_1_5x_fees"]


def test_more_drawdown_per_dollar_than_the_smaller_arm_fails():
    incs = _case(noisy(5.0), noisy(5.0, seed=2), dd=(0.02, 0.01, 0.005))
    assert incs["second"]["checks"]["mean_over_dd_at_least_smaller"] is False
    assert incs["second"]["verdict"] == "fail" and st.proposed_count(incs) == 1
    incs = _case(noisy(5.0), noisy(5.0, seed=2), dd=(0.01, None, 0.01))
    assert incs["second"]["checks"]["mean_over_dd_at_least_smaller"] is False


def test_too_few_sessions_is_no_verdict():
    incs = _case(noisy(5.0, n=99), noisy(5.0, n=99, seed=2))
    assert incs["second"]["verdict"] == "no verdict" and st.proposed_count(incs) == 1


def test_the_drawdown_is_bootstrapped_from_whole_sessions():
    nets = noisy(5.0, n=200, sd=40.0)
    dd = st.session_dd(sessions(nets))
    assert dd["drawdown_p99"] and dd["drawdown_p99"] > 0
    assert dd["mean_over_dd"] == pytest.approx(sum(nets) / len(nets) / dd["drawdown_p99"], rel=1e-4)
