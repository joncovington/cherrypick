"""Hypothesis 39's study (scripts/contango_item39.py): the signals, the runs, the measures, the pass
rule and the guards, on invented series. No store, no real file.

Each guard is shown firing: broken on purpose, it must report not-ok, because a check that cannot fire
reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from cherrypick.contango import regime, replay

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location("contango_item39", SCRIPTS / "contango_item39.py")
s39 = importlib.util.module_from_spec(_spec)
sys.modules["contango_item39"] = s39
_spec.loader.exec_module(s39)

CONTROL = {"enter_below": 0.97, "exit_at_or_above": 0.97}


def weekdays(n: int, start: date = date(2018, 3, 1)) -> list[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def make_data(n: int = 420, seed: int = 3, informative: bool = True) -> dict:
    """Blocks of 20 sessions; every third block is an inversion, in which SVXY loses 3% a day. The
    regime is known at the close of the session before the loss, so a switch can avoid it. With
    `informative` False the ratio is random against the returns."""
    rng = random.Random(seed)
    days = weekdays(n + 10)  # ten sessions of history before the first candidate, for the realised volatility
    inv = [(i // 20) % 3 == 2 for i in range(n + 10)]
    signal_inv = inv if informative else [rng.random() < 0.33 for _ in range(n + 10)]
    risk, cash, vix, vix3m, vix9d, spx = {}, {}, {}, {}, {}, {}
    r_px, c_px, s_px = 100.0, 100.0, 4000.0
    for i, d in enumerate(days):
        if i:
            bad = inv[i - 1]
            r_px *= 1 + (-0.03 if bad else 0.004) + rng.gauss(0, 0.003)
            c_px *= 1.0001
            s_px *= 1 + rng.gauss(0, 0.002)
        risk[d], cash[d], spx[d] = r_px, c_px, s_px
        v = 20.0 + rng.gauss(0, 0.2)
        vix[d] = v
        vix3m[d] = v / (1.05 if signal_inv[i] else 0.90)
        vix9d[d] = v * 0.9
    return {
        "risk": risk,
        "cash": cash,
        "inputs": {"vix": vix, "vix3m": vix3m, "vix9d": vix9d, "spx": spx},
        "calendar": days,
        "candidates": days[10:],
    }


def reference_for(data: dict, params_override: dict | None = None):
    """The module's own replay on the ratio, as `contango_replay.run_arms` runs it."""
    ratio = {d: data["inputs"]["vix"][d] / data["inputs"]["vix3m"][d] for d in data["inputs"]["vix"]}

    def ref(arm: str, days: list[str], cost_bps: float):
        if arm not in ("control", "flipexit"):
            return None
        params = (params_override or {}).get(arm, s39.PARAMS[arm])
        span = [d for d in sorted(data["risk"]) if days[0] <= d <= days[-1]]
        return replay.run(span, ratio, data["risk"], data["cash"], params, cost_bps=cost_bps)["navs"]

    return ref


def windows_for(data: dict) -> dict:
    days = data["candidates"]
    return {"design": (days[0], days[299]), "holdout": (days[300], None)}


def study(data: dict, **kw):
    days = data["candidates"]
    return s39.run_study(
        data,
        windows=windows_for(data),
        split=days[150],
        split_day=days[100],
        crash=(days[40], days[70]),
        resamples=200,
        reference=reference_for(data),
        **kw,
    )


# ---------------------------------------------------------------------------- signals


def test_the_ratio_is_the_unrounded_division():
    assert s39.ratio_series({"a": 18.0, "b": 5.0}, {"a": 20.0, "b": 0.0}) == {"a": 0.9}


def test_realised_volatility_is_the_sample_deviation_of_log_returns_in_vol_points():
    closes = [100.0 * (1.01 if i % 2 else 1.0) ** i for i in range(11)]
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:], strict=False)]
    mean = sum(rets) / 10
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / 9)
    assert s39.realised_vol(closes) == pytest.approx(sd * math.sqrt(252) * 100)
    assert s39.realised_vol([100.0] * 11) == 0.0
    assert s39.realised_vol([100.0, 0.0, 100.0]) is None


def test_it_agrees_with_the_overview_reader():
    files = pytest.importorskip("cherrypick.overview.files")
    closes = [100.0, 101.0, 99.5, 100.5, 102.0, 101.0, 103.0, 102.0, 104.0, 103.0, 105.0]
    assert s39.realised_vol(closes) == pytest.approx(files.realized_vol(closes, 10))


def test_realised_volatility_needs_ten_returns_ending_on_the_day():
    cal = weekdays(14)
    spx = {d: 4000.0 * (1.001 if i % 2 else 0.999) ** i for i, d in enumerate(cal)}
    rv = s39.realised_series(spx, cal)
    assert min(rv) == cal[10]  # eleven closes: index 0..10
    assert cal[9] not in rv


def test_a_missing_close_inside_the_window_leaves_the_day_out():
    cal = weekdays(25)
    spx = {d: 4000.0 * (1.001 if i % 2 else 0.999) ** i for i, d in enumerate(cal)}
    del spx[cal[12]]
    gapped = s39.realised_series(spx, cal)
    assert [d for d in cal if d in gapped] == cal[10:12] + cal[23:]  # each window reaches back ten sessions


def _inputs(vix, vix3m, vix9d, spx=None, n=1):
    days = weekdays(n)
    return (
        {
            "vix": dict(zip(days, vix, strict=True)),
            "vix3m": dict(zip(days, vix3m, strict=True)),
            "vix9d": dict(zip(days, vix9d, strict=True)),
            "spx": spx or {},
        },
        days,
    )


def test_control_and_flipexit_read_the_ratio():
    inputs, days = _inputs([18, 20], [20, 20], [17, 17], n=2)
    for arm in ("control", "flipexit"):
        assert s39.signals(arm, inputs, days) == {days[0]: 0.9, days[1]: 1.0}


def test_the_nine_day_exit_needs_the_ratio_and_vix9d_strictly_below_vix():
    inputs, days = _inputs([20, 20, 20], [25, 25, 25], [19.99, 20.0, 21], n=3)
    sig = s39.signals("vix9d-exit", inputs, days)
    assert sig[days[0]] == 0.8  # both clear
    assert sig[days[1]] == s39.OFF  # equal is not below
    assert sig[days[2]] == s39.OFF
    inputs2, days2 = _inputs([20], [19], [15], n=1)  # the 9-day clears but the ratio does not
    assert s39.signals("vix9d-exit", inputs2, days2)[days2[0]] == pytest.approx(20 / 19)
    assert s39.run_arm(days2, {days2[0]: 20 / 19}, {days2[0]: 1.0}, {days2[0]: 1.0}, CONTROL, 0)[
        "states"
    ] == [regime.CASH]


def test_the_realised_volatility_gate_is_strictly_vix_above_realised():
    cal = weekdays(12)
    spx = {d: 4000.0 * (1.01 if i % 2 else 0.99) ** i for i, d in enumerate(cal)}
    rv = s39.realised_series(spx, cal)[cal[11]]
    inputs = {"vix": dict.fromkeys(cal, 20.0), "vix3m": dict.fromkeys(cal, 25.0), "vix9d": {}, "spx": spx}
    last = cal[11]

    def gate(vix: float) -> float:
        inputs["vix"][last] = vix
        inputs["vix3m"][last] = vix / 0.8
        return s39.signals("rv-gate", inputs, cal)[last]

    assert gate(rv + 0.001) == pytest.approx(0.8)
    assert gate(rv - 0.001) == s39.OFF
    assert gate(rv) == s39.OFF  # equal is not above
    assert cal[5] not in s39.signals("rv-gate", inputs, cal)  # no ten returns yet


def test_a_lag_gives_each_day_the_previous_days_signal():
    cal = weekdays(4)
    sig = dict(zip(cal, [0.1, 0.2, 0.3, 0.4], strict=True))
    assert s39.lagged(sig, cal) == {cal[1]: 0.1, cal[2]: 0.2, cal[3]: 0.3}
    del sig[cal[1]]
    assert s39.lagged(sig, cal) == {cal[1]: 0.1, cal[3]: 0.3}


# ---------------------------------------------------------------------------- runs


def test_flipexit_holds_through_the_band_and_control_does_not():
    days = weekdays(5)
    sig = dict(zip(days, [0.9, 0.98, 0.98, 1.0, 0.9], strict=True))
    flat = dict.fromkeys(days, 100.0)
    ctl = s39.run_arm(days, sig, flat, flat, s39.PARAMS["control"], 0)
    flip = s39.run_arm(days, sig, flat, flat, s39.PARAMS["flipexit"], 0)
    assert ctl["states"] == [regime.RISK, regime.CASH, regime.CASH, regime.CASH, regime.RISK]
    assert flip["states"] == [regime.RISK, regime.RISK, regime.RISK, regime.CASH, regime.RISK]
    assert (ctl["switches"], flip["switches"]) == (2, 2)


def test_a_run_is_the_modules_replay_with_its_states_and_sides():
    data = make_data(80)
    days = data["candidates"]
    sig = s39.signals("control", data["inputs"], days)
    run = s39.run_arm(days, sig, data["risk"], data["cash"], CONTROL, 10)
    ref = replay.run(days, sig, data["risk"], data["cash"], CONTROL, cost_bps=10)
    assert run["navs"] == ref["navs"]
    assert run["switches"] == ref["switches"] == run["replay_switches"]
    assert run["sides"] == 2 * run["switches"] + 1
    assert sum(h == regime.RISK for h in run["held"]) == ref["days_in_risk"]
    assert run["rets"][0] == pytest.approx(-0.001)  # the opening side
    assert len(run["rets"]) == len(run["excess"]) == len(days)


def test_the_baselines_hold_one_fund_and_pay_one_side():
    data = make_data(40)
    days = data["candidates"]
    svxy = s39.hold_run("hold-svxy", days, data["risk"], data["cash"], 10)
    shv = s39.hold_run("hold-shv", days, data["risk"], data["cash"], 10)
    assert svxy["switches"] == shv["switches"] == 0 and svxy["sides"] == shv["sides"] == 1
    assert set(svxy["states"]) == {regime.RISK} and set(shv["states"]) == {regime.CASH}
    assert svxy["navs"][-1][1] == pytest.approx((1 - 0.001) * data["risk"][days[-1]] / data["risk"][days[0]])
    assert all(abs(e) < 1e-12 for e in shv["excess"][1:])  # excess over SHV's own return is zero


def test_a_whipsaw_is_a_switch_undone_within_five_sessions():
    assert s39.whipsaws([3, 6, 20, 40, 44, 46]) == 3  # (3,6) (40,44) (44,46)
    assert s39.whipsaws([3, 9]) == 0
    assert s39.whipsaws([]) == 0


def test_sessions_missing_an_input_are_skipped_and_counted():
    days = weekdays(6)
    risk, cash = dict.fromkeys(days, 100.0), dict.fromkeys(days, 100.0)
    sig = dict.fromkeys(days, 0.9)
    del sig[days[2]]
    del cash[days[4]]
    usable, missing = s39.arm_days(sig, risk, cash, days)
    assert usable == [days[0], days[1], days[3], days[5]] and missing == [days[2], days[4]]
    inputs = {
        "vix": {d: 1.0 for d in days},
        "vix3m": {d: 1.0 for d in days if d != days[2]},
        "vix9d": {},
        "spx": {},
    }
    assert s39.missing_inputs("control", inputs, risk, cash, missing) == {"vix3m": 1, "shv": 1}
    assert s39.non_session_days(days + ["2018-03-17"], {**inputs, "vix9d": {}, "spx": {}}) == {"2018-03-17"}


def test_windows_cut_the_candidates_and_drop_non_sessions():
    days = weekdays(10)
    assert s39.window_days(days, {days[3]}, days[2], days[5]) == [days[2], days[4], days[5]]
    assert s39.window_days(days, set(), days[8], None) == days[8:]


# ---------------------------------------------------------------------------- measures


def test_the_measures_on_a_known_path():
    rets = [0.10, -0.20, 0.05, 0.05, 0.05, 0.05, 0.05]
    nav = 1.0
    for r in rets:
        nav *= 1 + r
    assert s39.max_drawdown(rets) == pytest.approx(-0.20)
    assert s39.max_drawdown([0.1, 0.1]) == 0.0
    days = ["2020-01-01", "2020-07-01"]
    assert s39.cagr([1.0], days) == pytest.approx(
        2.0 ** (1 / ((date(2020, 7, 1) - date(2020, 1, 1)).days / 365.25)) - 1
    )
    assert s39.worst_week([0.1, -0.1, -0.1, 0.0, 0.0, 0.0, 0.1]) == pytest.approx(0.9 * 0.9 - 1)
    assert s39.worst_week([0.1, 0.2], 5) is None


def test_sharpe_is_the_annualised_mean_over_the_sample_deviation():
    x = [0.01, -0.01, 0.02, 0.0]
    mean = sum(x) / 4
    sd = math.sqrt(sum((v - mean) ** 2 for v in x) / 3)
    assert s39.sharpe(x) == pytest.approx(mean / sd * math.sqrt(252))
    assert s39.sharpe([0.01, 0.01, 0.01]) is None
    assert s39.sharpe([0.01]) is None


def test_the_bootstrap_is_seeded_and_centres_on_the_difference():
    rng = random.Random(1)
    base = [rng.gauss(0.0004, 0.01) for _ in range(300)]
    arm = [b + 0.002 for b in base]
    a = s39.bootstrap_sharpe_diff(arm, base, resamples=300)
    b = s39.bootstrap_sharpe_diff(arm, base, resamples=300)
    assert a == b
    assert a["diff"] == pytest.approx(s39.sharpe_diff(arm, base)) and a["diff"] > 0
    assert a["lower"] > 0 and a["p"] < 0.05
    same = s39.bootstrap_sharpe_diff(base, base, resamples=100)
    assert same["lower"] == 0.0 and same["p"] == 1.0  # no difference reads as no evidence
    worse = s39.bootstrap_sharpe_diff(base, arm, resamples=300)
    assert worse["diff"] < 0 and worse["p"] > 0.95


def test_the_crash_window_is_dropped_and_the_rest_joined():
    s = {"days": weekdays(6), "rets": [1, 2, 3, 4, 5, 6], "excess": [1, 2, 3, 4, 5, 6]}
    out = s39.drop_days(s, s["days"][1], s["days"][3])
    assert out["rets"] == [1, 5, 6] and out["days"] == [s["days"][0], s["days"][4], s["days"][5]]


def test_a_slice_keeps_the_days_it_is_asked_for():
    data = make_data(60)
    days = data["candidates"]
    run = s39.run_arm(
        days, s39.signals("control", data["inputs"], days), data["risk"], data["cash"], CONTROL, 10
    )
    part = s39.slice_run(run, days[10], days[19])
    assert part["days"] == days[10:20] and part["rets"] == run["rets"][10:20]


def test_return_by_year_compounds():
    out = s39.by_year({"days": ["2019-12-30", "2019-12-31", "2020-01-02"], "rets": [0.1, 0.1, 0.5]})
    assert out["2019"] == pytest.approx(0.21) and out["2020"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------- the rule, clause by clause


def fig(**kw) -> dict:
    base = {
        "sharpe_diff": 0.4,
        "lower": 0.1,
        "arm_dd": -0.2,
        "base_dd": -0.5,
        "arm_cagr": 0.08,
        "base_cagr": 0.10,
        "halves": [
            {"sharpe_diff": 0.3, "arm_dd": -0.2, "base_dd": -0.4},
            {"sharpe_diff": 0.2, "arm_dd": -0.1, "base_dd": -0.5},
        ],
        "crash_diff": 0.2,
        "lag_diff": 0.1,
    }
    base.update(kw)
    return base


def test_every_clause_can_fail_alone():
    assert all(s39.design_checks(fig(), True).values())
    cases = {
        "sharpe": [fig(sharpe_diff=-0.1), fig(lower=-0.01), fig(lower=None)],
        "drawdown": [fig(arm_dd=-0.5), fig(arm_dd=-0.6)],
        "floor": [fig(arm_cagr=0.06), fig(arm_cagr=None)],
        "halves": [
            fig(halves=[{"sharpe_diff": -0.1, "arm_dd": -0.2, "base_dd": -0.4}, fig()["halves"][1]]),
            fig(halves=[fig()["halves"][0], {"sharpe_diff": 0.2, "arm_dd": -0.6, "base_dd": -0.5}]),
        ],
        "crash": [fig(crash_diff=-0.01), fig(crash_diff=None)],
        "lag": [fig(lag_diff=0.0), fig(lag_diff=None)],
    }
    for clause, figs in cases.items():
        for f in figs:
            checks = s39.design_checks(f, True)
            assert not checks[clause], (clause, f)
            assert [k for k, v in checks.items() if not v] == [clause], (clause, f)
    assert not s39.design_checks(fig(), False)["sharpe"]  # Holm refuses


def test_the_floor_is_three_points_of_cagr():
    assert s39.design_checks(fig(arm_cagr=0.0701), True)["floor"]
    assert not s39.design_checks(fig(arm_cagr=0.0699), True)["floor"]


def test_the_holdout_needs_sharpe_and_the_floor():
    ok = {"sharpe_diff": 0.2, "arm_cagr": 0.05, "base_cagr": 0.07}
    assert all(s39.holdout_checks(ok).values())
    assert not s39.holdout_checks({**ok, "sharpe_diff": -0.1})["sharpe"]
    assert not s39.holdout_checks({**ok, "arm_cagr": 0.03})["floor"]


def holdout(**kw) -> dict:
    return {"checks": {"sharpe": True, "floor": True}, "coverage_ok": True, "informative": True, **kw}


def test_a_verdict_follows_the_declared_order():
    design = {"checks": dict.fromkeys(s39.CLAUSES, True), "coverage_ok": True}
    assert s39.judge_arm(design, holdout())["verdict"] == "pass"
    failed = {"checks": {**design["checks"], "lag": False}, "coverage_ok": True}
    v = s39.judge_arm(failed, holdout())
    assert v["verdict"] == "fail" and "lag" in v["why"]  # a holdout cannot overturn a design fail
    assert s39.judge_arm({**design, "coverage_ok": False}, holdout())["verdict"] == "no verdict"
    assert s39.judge_arm(design, holdout(coverage_ok=False))["verdict"] == "no verdict"
    assert s39.judge_arm(design, holdout(informative=False))["verdict"] == "no verdict"
    assert s39.judge_arm(design, holdout(checks={"sharpe": False, "floor": True}))["verdict"] == "fail"
    assert s39.judge_arm(design, None)["verdict"] == "no verdict"
    assert s39.judge_arm(design, holdout(), why="a guard failed")["verdict"] == "no verdict"


def test_the_base_candidate_is_the_passing_arm_with_the_higher_difference():
    v = {a: {"verdict": "pass"} for a in ("control", "flipexit")} | {"rv-gate": {"verdict": "fail"}}
    assert s39.base_candidate(v, {"control": 0.4, "flipexit": 0.6, "rv-gate": 9.0}) == "flipexit"
    assert s39.base_candidate({"control": {"verdict": "fail"}}, {"control": 1.0}) is None


def test_a_missing_share_over_one_percent_is_too_many():
    assert s39.missing_share(12, 1215) <= s39.MISSING_MAX
    assert s39.missing_share(13, 1215) > s39.MISSING_MAX
    assert s39.missing_share(0, 0) > s39.MISSING_MAX


# ---------------------------------------------------------------------------- the study end to end


def test_a_gate_that_sees_the_inversions_passes_and_the_holdout_is_read():
    out = study(make_data())
    assert all(g["ok"] for g in out["guards"].values()), out["guards"]
    assert {a: v["verdict"] for a, v in out["verdict"].items()} == dict.fromkeys(s39.ARMS, "pass")
    assert set(out["holdout"]["arms"]) == set(s39.ARMS)
    assert out["base_candidate"] in s39.ARMS
    a = out["design"]["arms"]["control"]
    assert a["10"]["arm"]["max_drawdown"] > a["10"]["base"]["max_drawdown"]
    assert a["bootstrap"]["lower"] > 0 and a["holm_p"] <= 0.05
    assert a["crash"]["removed"] == 31 and a["lag"]["sharpe_diff"] > 0
    assert len(a["halves"]) == 2 and a["2"]["arm"]["switches"] == a["10"]["arm"]["switches"]


def test_a_signal_with_no_information_fails_and_the_holdout_stays_unread():
    out = study(make_data(informative=False))
    assert {v["verdict"] for v in out["verdict"].values()} == {"fail"}
    assert out["holdout"]["arms"] == {}
    assert out["base_candidate"] is None


def test_an_arm_short_of_inputs_has_no_verdict():
    data = make_data()
    for d in data["candidates"][:30]:  # 10% of the design window
        data["inputs"]["vix9d"].pop(d)
    out = study(data)
    assert out["verdict"]["vix9d-exit"]["verdict"] == "no verdict"
    assert out["design"]["arms"]["vix9d-exit"]["coverage"]["missing"] == 30
    assert out["verdict"]["control"]["verdict"] == "pass"


def test_a_failed_guard_stops_every_arm_from_being_read():
    data = make_data()
    day = data["candidates"][30]  # a ratio between 0.97 and 0.98, where a moved threshold decides differently
    data["inputs"]["vix3m"][day] = data["inputs"]["vix"][day] / 0.975
    out = s39.run_study(
        data,
        windows=windows_for(data),
        resamples=50,
        split_day=data["candidates"][100],
        reference=reference_for(data, {"control": {"enter_below": 0.98, "exit_at_or_above": 0.98}}),
    )
    assert not out["guards"]["reproduction control"]["ok"]
    assert {v["verdict"] for v in out["verdict"].values()} == {"no verdict"}
    assert "design" not in out
    assert "A guard failed" in s39.report(out)


def test_the_result_reads_on_the_board():
    hyp = pytest.importorskip("cherrypick.orchestrator.hypotheses")
    out = study(make_data())
    words = hyp.verdicts(out)
    assert {w["arm"] for w in words} == set(s39.ARMS) and {w["verdict"] for w in words} == {"pass"}
    assert isinstance(out["window"], str) and len(out["span"]) == 2


def test_the_report_states_the_verdict_and_works_the_reading_from_the_figures():
    text = s39.report(study(make_data(informative=False)))
    assert text.startswith("# Item 39")
    assert "**Verdict: fail.**" in text
    assert "## What it says" in text and "## How to read the tables" in text
    assert "It misses on:" in text
    assert "No arm passes" in text and "a holdout cannot overturn a design fail" in text
    assert "Seen before declaring" in text
    passed = s39.report(study(make_data()))
    assert "**Verdict: pass.**" in passed and "Every design clause holds." in passed
    assert "Holdout" in passed and "### Reported at 2 bps" in passed


def test_the_plan_names_a_stale_index_file():
    data = make_data(30)
    assert s39.plan_facts(data, windows_for(make_data()))["stale"] == []
    early = s39.plan_facts(data, windows_for(make_data()))["windows"]["design"]["arms"]["rv-gate"]["missing"]
    assert early == 0
    for d in data["candidates"][-3:]:
        data["inputs"]["spx"].pop(d)
        data["inputs"]["vix9d"].pop(d)
    facts = s39.plan_facts(data, windows_for(make_data()))
    assert facts["stale"] == ["VIX9D", "SPX"]
    assert facts["windows"]["design"]["arms"]["rv-gate"]["missing"] == early + 3
    assert facts["windows"]["design"]["arms"]["control"]["missing"] == 0


# ---------------------------------------------------------------------------- each guard, shown to fail


def _run(data, arm="control", cost=10.0):
    days = data["candidates"]
    sig = s39.signals(arm, data["inputs"], data["calendar"])
    return days, sig, s39.run_arm(days, sig, data["risk"], data["cash"], s39.PARAMS[arm], cost)


def test_guard_reproduction_fires_on_a_moved_threshold():
    data = make_data(120)
    days = data["candidates"]
    # the ratio never sits between 0.97 and 0.98 in this data; put one day there
    data["inputs"]["vix3m"][days[30]] = data["inputs"]["vix"][days[30]] / 0.975
    _, sig, run = _run(data)
    assert s39.check_reproduction(run["navs"], reference_for(data)("control", days, 10.0))["ok"]
    moved = reference_for(data, {"control": {"enter_below": 0.98, "exit_at_or_above": 0.98}})
    assert not s39.check_reproduction(run["navs"], moved("control", days, 10.0))["ok"]
    assert not s39.check_reproduction(run["navs"], None)["ok"]
    assert not s39.check_reproduction(run["navs"], run["navs"][:-1])["ok"]


def test_guard_accounting_fires_when_a_side_of_cost_is_dropped():
    data = make_data(120)
    days, sig, run = _run(data)
    assert run["switches"] >= 2
    assert s39.check_accounting(run, data["risk"], data["cash"])["ok"]
    cheap = s39.run_arm(days, sig, data["risk"], data["cash"], CONTROL, 5.0)  # half the cost
    assert not s39.check_accounting({**run, "navs": cheap["navs"]}, data["risk"], data["cash"])["ok"]
    assert not s39.check_accounting({**run, "sides": run["sides"] - 1}, data["risk"], data["cash"])["ok"]


def test_guard_no_look_ahead_fires_on_a_signal_that_reads_tomorrow():
    data = make_data(80)
    cal, inputs = data["calendar"], data["inputs"]
    probe = cal[39]  # the last session of a block: the next one is a different regime
    assert s39.check_causal(s39.signals, "control", inputs, cal, probe)["ok"]
    assert s39.check_causal(s39.signals, "rv-gate", inputs, cal, probe)["ok"]

    def peeking(arm, inp, calendar):
        ratio = s39.ratio_series(inp["vix"], inp["vix3m"])
        ds = sorted(ratio)
        return {d: ratio[ds[min(i + 1, len(ds) - 1)]] for i, d in enumerate(ds)}  # tomorrow's close

    assert not s39.check_causal(peeking, "control", inputs, cal, probe)["ok"]

    def blind(arm, inp, calendar):  # ignores its inputs, so shifting them changes nothing
        return dict.fromkeys(calendar, 0.9)

    assert not s39.check_causal(blind, "control", inputs, cal, probe)["ok"]


def test_a_signal_moved_back_one_day_changes_the_result():
    data = make_data(80)
    cal, inputs = data["calendar"], data["inputs"]
    now = s39.signals("control", inputs, cal)
    back = s39.signals("control", s39.shifted_inputs(inputs, cal), cal)
    run_now = s39.run_arm(cal, now, data["risk"], data["cash"], CONTROL, 10)
    usable = [d for d in cal if d in back]
    run_back = s39.run_arm(usable, back, data["risk"], data["cash"], CONTROL, 10)
    assert run_now["navs"][-1][1] != run_back["navs"][-1][1]


def test_guard_split_fires_on_the_raw_closes():
    days = ["2024-04-09", "2024-04-10", "2024-04-11", "2024-04-12"]
    adjusted = dict(zip(days, [30.0, 30.3, 30.6, 30.2], strict=True))
    raw = dict(zip(days, [60.0, 60.6, 30.6, 30.2], strict=True))  # the 2-for-1 shows as a halving
    assert s39.check_split(adjusted)["ok"]
    assert not s39.check_split(raw)["ok"]
    assert not s39.check_split({"2024-04-10": 30.0})["ok"]


def test_guard_coverage_fires_on_a_zero_filled_gap():
    data = make_data(80)
    days = data["candidates"]
    delete = days[40]
    assert s39.check_skip("control", data, days, delete)["ok"]

    def zero_fill(signal, risk, cash, window):  # keeps every day, so a deleted input earns a zero
        return list(window), []

    assert not s39.check_skip("control", data, days, delete, days_fn=zero_fill)["ok"]
    assert not s39.check_skip("control", data, days, "2018-01-01")["ok"]
