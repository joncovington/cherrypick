"""Item 34's rules (cherrypick.backtest.ratiostudy) and its script (scripts/option_stock_ratio_item34.py),
on invented data only: no store, no vendor figure.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest
from cherrypick.core import robustness

from cherrypick.backtest import flow, ratiostudy
from cherrypick.backtest.ratiostudy import Scan

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "option_stock_ratio_item34", SCRIPTS / "option_stock_ratio_item34.py"
)
s34 = importlib.util.module_from_spec(_spec)
sys.modules["option_stock_ratio_item34"] = s34
_spec.loader.exec_module(s34)


@pytest.fixture(autouse=True)
def _quick_bootstrap(monkeypatch):
    monkeypatch.setattr(ratiostudy, "RESAMPLES", 500)


def hist(*pairs: tuple[float, int]) -> list[int]:
    h = [0] * flow.N_BUCKETS
    for premium, n in pairs:
        h[flow.bucket(premium)] += n
    return h


def row(sym, ratio, ex, dvol=1e6, prior=0.0, reported=False):
    return {"sym": sym, "ratio": ratio, "ex": ex, "dvol": dvol, "prior": prior, "reported": reported}


def make_scan(n_sessions, build):
    grid = [f"s{k:03d}" for k in range(n_sessions)]
    return Scan(grid=grid, rows={s: build(k) for k, s in enumerate(grid)})


def effect_rows(k, n=30, amp=0.05, flip=False, reported_ends=False, dvol=None, prior=None):
    """`n` names with ratio i; the outcome falls as the ratio rises (the declared direction) unless
    `flip`. A little session-to-session variation keeps the interval honest."""
    wobble = 1.0 + 0.1 * (k % 3)
    out = []
    for i in range(n):
        ex = amp * wobble * (1 - 2 * i / (n - 1)) * (-1 if flip else 1)
        end = i < 3 or i >= n - 3
        out.append(
            row(
                f"N{i:02d}",
                float(i),
                ex,
                dvol=dvol(i) if dvol else 1e6,
                prior=prior(i) if prior else 0.0,
                reported=reported_ends and end,
            )
        )
    return out


def passing_scan(n=120):
    return make_scan(n, effect_rows)


# ---- the histogram to premium ----


def test_a_bucket_is_counted_at_its_geometric_midpoint():
    i = flow.bucket(1000.0)
    lo, hi = flow.bucket_floor(i), flow.bucket_floor(i + 1)
    assert ratiostudy.bucket_value(i) == pytest.approx(math.sqrt(lo * hi))
    assert lo < ratiostudy.bucket_value(i) < hi  # not the floor, not the ceiling
    assert ratiostudy.premium_of(hist((1000.0, 3))) == pytest.approx(3 * ratiostudy.bucket_value(i))


def test_premium_sums_across_buckets_and_ends_have_declared_values():
    two = hist((1000.0, 2), (200_000.0, 1))
    expect = 2 * ratiostudy.bucket_value(flow.bucket(1000.0)) + ratiostudy.bucket_value(
        flow.bucket(200_000.0)
    )
    assert ratiostudy.premium_of(two) == pytest.approx(expect)
    assert ratiostudy.bucket_value(0) == 50.0  # underflow at half of $100
    assert ratiostudy.bucket_value(flow.N_BUCKETS - 1) == pytest.approx(1e7)  # overflow at its floor
    assert ratiostudy.premium_of([0] * flow.N_BUCKETS) == 0.0


def test_ratio_is_premium_over_raw_close_times_volume():
    h = hist((1000.0, 4))
    prem = ratiostudy.premium_of(h)
    assert ratiostudy.ratio_of(h, 50.0, 2_000_000.0) == pytest.approx(prem / 1e8)
    assert ratiostudy.ratio_of(h, 50.0, 0) is None
    assert ratiostudy.ratio_of(h, None, 1e6) is None
    assert ratiostudy.ratio_of(h, 50.0, None) is None


def test_a_zero_premium_name_day_has_no_ratio_and_never_enters_a_decile(monkeypatch):
    empty = [0] * flow.N_BUCKETS
    assert ratiostudy.ratio_of(empty, 50.0, 1e6) is None
    assert ratiostudy.ratio_of(hist((1000.0, 1)), 50.0, 1e6) > 0

    # the rule as the script applies it: ratio None means the row is never built, so the decile is
    # drawn from the 20 names that traded; 5 inactive names would otherwise fill the low decile
    def build(k):
        rows = effect_rows(k, n=20)
        for r in rows:
            r["ratio"] += 1.0
        for j in range(5):
            r0 = ratiostudy.ratio_of(empty, 50.0, 1e6)
            if r0 is not None:
                rows.append(row(f"Z{j}", r0, 0.0))
        return rows

    scan = make_scan(3, build)
    low, _ = ratiostudy.deciles(scan.rows["s000"])
    assert not any(r["sym"].startswith("Z") for r in low) and len(scan.rows["s000"]) == 20
    # break the rule (zero premium read as ratio 0): the inactive names crowd the lowest decile
    monkeypatch.setattr(ratiostudy, "premium_of", lambda h: 0.0 if not any(h) else 1.0)
    monkeypatch.setattr(
        ratiostudy,
        "ratio_of",
        lambda h, c, v: 0.0 if not any(h) else 1.0,
    )
    scan = make_scan(3, build)
    low, _ = ratiostudy.deciles(scan.rows["s000"])
    assert any(r["sym"].startswith("Z") for r in low)


# ---- deciles within a session ----


def test_deciles_are_ranked_within_the_session_with_ties_by_symbol():
    rows = [row(f"S{i:02d}", float(i % 20 // 1), 0.0) for i in range(40)]
    low, high = ratiostudy.deciles(rows)
    assert len(low) == len(high) == 4
    assert all(r["ratio"] <= 1.0 for r in low) and all(r["ratio"] >= 18.0 for r in high)
    # the two ratio-0 names, S00 and S20, come first by symbol, then ratio 1
    assert [r["sym"] for r in low] == ["S00", "S20", "S01", "S21"]


def test_deciles_need_ten_names_and_take_a_tenth_each():
    assert ratiostudy.deciles([row(f"S{i}", float(i), 0.0) for i in range(9)]) is None
    low, high = ratiostudy.deciles([row(f"S{i:02d}", float(i), 0.0) for i in range(19)])
    assert len(low) == len(high) == 1  # 19 // 10
    low, high = ratiostudy.deciles([row(f"S{i:02d}", float(i), 0.0) for i in range(105)])
    assert len(low) == len(high) == 10


def test_a_decile_is_not_pooled_across_sessions():
    # session A's ratios all dwarf session B's; per-session ranking still puts one name each end
    a = [row(f"S{i}", 1000.0 + i, 0.01) for i in range(10)]
    b = [row(f"S{i}", float(i), -0.01) for i in range(10)]
    scan = Scan(grid=["a", "b"], rows={"a": a, "b": b})
    assert ratiostudy.series(scan, ["a", "b"])["sessions"] == ["a", "b"]


# ---- the spread and the costs ----


def test_spread_is_lowest_decile_less_highest_and_costs_are_four_sides():
    rows = [row(f"S{i:02d}", float(i), 0.0) for i in range(20)]
    rows[0]["ex"], rows[1]["ex"] = 0.03, 0.01  # lowest decile: mean 0.02
    rows[18]["ex"], rows[19]["ex"] = -0.01, -0.03  # highest decile: mean -0.02
    assert ratiostudy.session_gross(rows) == pytest.approx(0.04)
    assert ratiostudy.cost() == pytest.approx(0.004)  # 0.10% x (enter, exit) x (long, short)
    assert ratiostudy.net([0.04])[0] == pytest.approx(0.036)
    assert ratiostudy.cost(1.5) == pytest.approx(0.006)


def test_the_spread_is_long_low_short_high_not_the_reverse():
    scan = make_scan(3, lambda k: effect_rows(k, flip=True))
    assert ratiostudy.session_gross(scan.rows["s000"]) < 0
    scan = make_scan(3, effect_rows)
    assert ratiostudy.session_gross(scan.rows["s000"]) > 0


def test_a_session_without_both_deciles_is_left_out():
    scan = Scan(grid=["a", "b"], rows={"a": effect_rows(0), "b": effect_rows(0, n=9)})
    assert ratiostudy.series(scan, scan.grid)["sessions"] == ["a"]


def test_long_only_reads_lowest_decile_against_the_session_mean():
    rows = [row(f"S{i:02d}", float(i), 0.0) for i in range(20)]
    rows[0]["ex"], rows[1]["ex"] = 0.03, 0.01
    assert ratiostudy.session_long_only(rows) == pytest.approx(0.02 - 0.04 / 20)


# ---- the pass rule ----


def test_a_clean_effect_passes_every_condition():
    res = ratiostudy.evaluate(passing_scan())
    assert res["checks"] and all(res["checks"].values())
    assert res["verdict"] == "pass" and res["sessions"] == 120
    assert res["mean_net"] == pytest.approx(res["mean_gross"] - 0.004)
    assert ratiostudy.passed(res) == ["ratio"]


def test_the_no_verdict_floor_is_one_hundred_sessions_with_both_deciles():
    assert ratiostudy.evaluate(passing_scan(99))["verdict"] == "no verdict"
    assert ratiostudy.evaluate(passing_scan(100))["verdict"] == "pass"
    # a hundred sessions of which some lack the names for a decile do not count
    scan = make_scan(100, lambda k: effect_rows(k, n=30 if k >= 5 else 9))
    assert ratiostudy.evaluate(scan)["verdict"] == "no verdict"
    # a no-verdict is not a fail: an effect the wrong way over few sessions is still "no verdict"
    assert ratiostudy.evaluate(make_scan(50, lambda k: effect_rows(k, flip=True)))["verdict"] == "no verdict"


def test_the_wrong_way_fails():
    res = ratiostudy.evaluate(make_scan(120, lambda k: effect_rows(k, flip=True)))
    assert res["verdict"] == "fail" and not res["checks"]["mean_positive"]
    assert ratiostudy.passed(res) == []


def test_costs_come_off_before_the_sign_is_read():
    # a gross spread of about 0.3% a session is under the 0.4% cost
    res = ratiostudy.evaluate(make_scan(120, lambda k: effect_rows(k, amp=0.0015)))
    assert res["mean_gross"] > 0 and res["mean_net"] < 0 and res["verdict"] == "fail"


def test_the_lower_bound_must_clear_zero_not_only_the_mean():
    noisy = [0.2, -0.19] * 60  # mean +0.005, bound well below zero
    assert ratiostudy.lower_bound(noisy, resamples=500) < 0
    assert ratiostudy.lower_bound([0.01] * 60, resamples=500) > 0
    # and through the verdict: a positive-mean, noisy spread fails on the bound alone
    res = ratiostudy.evaluate(make_scan(120, lambda k: effect_rows(k, amp=0.2 if k % 2 == 0 else -0.19)))
    assert res["checks"]["mean_positive"] and not res["checks"]["lower_bound_positive"]
    assert res["verdict"] == "fail"


def test_both_time_halves_must_be_positive():
    # the effect for the first 60 sessions, the reverse after: the whole is a wash, so make the first
    # half much stronger so the mean stays positive
    scan = make_scan(120, lambda k: effect_rows(k, amp=0.08 if k < 60 else 0.0, flip=False))
    res = ratiostudy.evaluate(scan)
    assert res["checks"]["mean_positive"] and res["checks"]["lower_bound_positive"]
    assert not res["checks"]["second_half_positive"]  # second half is -cost
    assert res["verdict"] == "fail"
    first, second = ratiostudy.halves([1.0, 2.0, 3.0, 4.0, 5.0])
    assert first == [1.0, 2.0] and second == [3.0, 4.0, 5.0]


def _half_names():
    """Symbols of each name-half, from the suite's own fixed hash."""
    h = {0: [], 1: []}
    i = 0
    while min(len(h[0]), len(h[1])) < 20:
        s = f"T{i:03d}"
        h[robustness.name_half(s)].append(s)
        i += 1
    return h[0][:20], h[1][:20]


def _two_half_rows(k, half1_effect):
    h0, h1 = _half_names()
    wobble = 1.0 + 0.1 * (k % 3)
    out = []
    for i in range(20):
        out.append(row(h0[i], 2.0 * i, wobble * -0.1 * i / 19))
        out.append(row(h1[i], 2.0 * i + 1, wobble * half1_effect * i / 19))
    return out


def test_both_name_halves_must_be_positive():
    # half 0 falls strongly with the ratio, half 1 rises slightly: pooled the spread is positive
    bad = ratiostudy.evaluate(make_scan(120, lambda k: _two_half_rows(k, +0.01)))
    assert bad["checks"]["mean_positive"] and bad["checks"]["lower_bound_positive"]
    assert bad["name_halves"]["name_half_0"]["mean"] > 0 > bad["name_halves"]["name_half_1"]["mean"]
    assert not bad["checks"]["name_halves_positive"] and bad["verdict"] == "fail"
    # the same names with half 1 falling too: the guard lets it through
    good = ratiostudy.evaluate(make_scan(120, lambda k: _two_half_rows(k, -0.1)))
    assert good["checks"]["name_halves_positive"] and good["verdict"] == "pass"


def test_the_name_half_is_the_suites_fixed_hash():
    assert ratiostudy.name_half_of({"sym": "SPY"}) == robustness.name_half("SPY")


def test_positive_at_one_and_a_half_times_the_costs():
    # gross 0.5% clears the 0.4% cost but not 0.6%
    scan = make_scan(120, lambda k: effect_rows(k, amp=0.0025))
    res = ratiostudy.evaluate(scan)
    assert res["mean_net"] > 0 and res["stressed"]["mean"] < 0
    assert not res["checks"]["stressed_costs_positive"] and res["verdict"] == "fail"
    strong = ratiostudy.evaluate(passing_scan())
    assert strong["stressed"]["mean"] == pytest.approx(strong["mean_gross"] - 0.006)


def test_report_windows_are_removed_before_the_deciles_are_drawn():
    # the effect sits only in names with a report inside the outcome window
    def build(k):
        rows = effect_rows(k, reported_ends=True)
        for r in rows:
            if not r["reported"]:
                r["ex"] = 0.0
        return rows

    res = ratiostudy.evaluate(make_scan(120, build))
    assert res["checks"]["mean_positive"] and res["checks"]["lower_bound_positive"]
    assert not res["checks"]["no_reports_positive"] and res["verdict"] == "fail"
    assert res["no_reports"]["mean"] == pytest.approx(-0.004)
    # the same effect with no report flags passes: removal, not the data, is what failed it
    control = make_scan(120, lambda k: [{**r, "reported": False} for r in build(k)])
    assert ratiostudy.evaluate(control)["verdict"] == "pass"


# ---- the reported readings ----


def test_terciles_split_each_session_by_the_key_lowest_first():
    rows = [row(f"S{i:02d}", float(i), 0.0, dvol=float(i)) for i in range(30)]
    t = ratiostudy.tercile_rows(rows, "dvol")
    assert [len(x) for x in t] == [10, 10, 10]
    assert (
        max(r["dvol"] for r in t[0])
        < min(r["dvol"] for r in t[1])
        < max(r["dvol"] for r in t[1])
        < min(r["dvol"] for r in t[2])
    )  # noqa: E501
    rows[0]["prior"] = None
    assert sum(len(x) for x in ratiostudy.tercile_rows(rows, "prior")) == 29


def test_dollar_volume_terciles_show_where_the_effect_lives_and_do_not_judge():
    # names with the lowest third of dollar volume carry the effect; the top third reverses it
    def build(k):
        wobble = 1.0 + 0.1 * (k % 3)
        out = []
        for t, amp in ((0, 0.05), (1, 0.0), (2, -0.05)):
            for i in range(30):
                out.append(
                    row(
                        f"T{t}N{i:02d}",
                        float(i),
                        wobble * amp * (1 - 2 * i / 29),
                        dvol=1e6 * 10 ** (t * 2) * (1 + i / 100),
                    )
                )  # noqa: E501
        return out

    res = ratiostudy.evaluate(make_scan(120, build))
    t = res["dollar_volume_terciles"]
    assert [x["tercile"] for x in t] == [1, 2, 3]
    assert t[0]["mean"] > 0 > t[2]["mean"]
    # a reading only: the verdict comes from the five conditions, whatever the terciles say
    assert res["verdict"] == ("pass" if all(res["checks"].values()) else "fail")


def test_prior_return_terciles_are_reported():
    scan = make_scan(120, lambda k: effect_rows(k, prior=lambda i: (i % 7) / 100))
    res = ratiostudy.evaluate(scan)
    assert len(res["prior_return_terciles"]) == 3
    assert all(x["sessions"] > 0 for x in res["prior_return_terciles"])


def test_long_only_is_reported_not_judged():
    res = ratiostudy.evaluate(passing_scan())
    assert res["long_only"]["sessions"] == 120
    assert res["long_only"]["mean_net"] == pytest.approx(res["long_only"]["mean_gross"] - 0.002)


# ---- the holdout ----


def test_the_holdout_reads_only_the_mean_after_costs():
    ok = ratiostudy.evaluate_holdout(passing_scan(40))
    assert ok["verdict"] == "pass" and set(ok["checks"]) == {"mean_positive"}
    bad = ratiostudy.evaluate_holdout(make_scan(40, lambda k: effect_rows(k, flip=True)))
    assert bad["verdict"] == "fail"
    assert ratiostudy.evaluate_holdout(passing_scan(10))["verdict"] == "no verdict"


# ---- the script: the spent-once guard ----


def _args(tmp_path, window, **kw):
    import argparse

    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"grid_sessions": []}), encoding="utf-8")
    return argparse.Namespace(window=window, out_dir=str(tmp_path / "out"), plan=str(plan),
                              flow_db=str(tmp_path / "flow.db"), **kw)  # fmt: skip


@pytest.fixture
def stub_scan(monkeypatch):
    calls = []

    def fake(window, plan, db):
        calls.append(window)
        return passing_scan(), {"rows": 1}

    monkeypatch.setattr(s34, "build_scan", fake)
    windows = {"design": ["2022-05-16", "2025-06-30"], "holdout": ["2025-07-01", "2026-10-12"]}
    monkeypatch.setattr(s34.settings, "load", lambda: {"windows": windows})
    return calls


def test_design_writes_a_top_level_verdict_and_window(tmp_path, stub_scan):
    assert s34.cmd_study(_args(tmp_path, "design")) == 0
    out = tmp_path / "out"
    data = json.loads((out / "results-design.json").read_text(encoding="utf-8"))
    assert data["verdict"] == "pass" and data["window"] == "design" and data["passed"] == ["ratio"]
    assert (out / "results-design.md").read_text(encoding="utf-8").startswith("# Item 34")
    orch = pytest.importorskip("cherrypick.orchestrator.hypotheses")
    assert orch.verdicts(data) == [{"arm": None, "verdict": "pass"}]


def test_the_holdout_is_refused_without_a_design_pass(tmp_path, stub_scan):
    args = _args(tmp_path, "holdout")
    assert s34.cmd_study(args) == 2  # no design result at all
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    (out / "results-design.json").write_text(json.dumps({"verdict": "fail", "passed": []}), encoding="utf-8")
    assert s34.cmd_study(args) == 2  # a design fail
    assert stub_scan == [] and not (out / "holdout-spent.json").exists()
    assert not (out / "results-holdout.json").exists()


def test_the_holdout_runs_once_after_a_pass_and_then_is_spent(tmp_path, stub_scan):
    assert s34.cmd_study(_args(tmp_path, "design")) == 0
    args = _args(tmp_path, "holdout")
    assert s34.cmd_study(args) == 0
    out = tmp_path / "out"
    assert json.loads((out / "results-holdout.json").read_text(encoding="utf-8"))["window"] == "holdout"
    assert s34.cmd_study(args) == 2  # spent: its result is there
    (out / "results-holdout.json").unlink()
    assert s34.cmd_study(args) == 2  # spent: the marker alone is enough
    assert stub_scan == ["design", "holdout"]


def test_the_spent_marker_is_written_before_anything_is_read(tmp_path, monkeypatch, stub_scan):
    assert s34.cmd_study(_args(tmp_path, "design")) == 0

    def boom(*a):
        raise RuntimeError("read failed")

    monkeypatch.setattr(s34, "build_scan", boom)
    with pytest.raises(RuntimeError):
        s34.cmd_study(_args(tmp_path, "holdout"))
    assert (tmp_path / "out" / "holdout-spent.json").exists()
    assert s34.cmd_study(_args(tmp_path, "holdout")) == 2  # a crashed read does not buy a second look


def test_the_guard_fails_when_its_invariant_is_broken(tmp_path, monkeypatch, stub_scan):
    # break it on purpose: a guard that always says "fine" lets the second holdout read through
    monkeypatch.setattr(s34, "spent", lambda out, window: None)
    assert s34.cmd_study(_args(tmp_path, "design")) == 0
    args = _args(tmp_path, "holdout")
    assert s34.cmd_study(args) == 0
    assert s34.cmd_study(args) == 0  # the broken guard reads the holdout twice
    assert stub_scan.count("holdout") == 2  # the real guard (above) allows one


# ---- report ----


def test_report_names_the_failed_conditions_and_drops_the_window_sections_on_holdout():
    res = ratiostudy.evaluate(make_scan(120, lambda k: effect_rows(k, amp=0.0025)))
    res.update(window="design", span=["2022-05-16", "2025-06-30"], grid_sessions=120)
    md = s34.report(res)
    assert "positive at 1.5x the costs" in md and "**Verdict:** fail" in md
    assert "Terciles of dollar volume" in md and "Terciles of prior five sessions' return" in md
    hold = ratiostudy.evaluate_holdout(passing_scan(40))
    hold.update(window="holdout", span=["2025-07-01", "2026-10-12"], grid_sessions=40)
    hmd = s34.report(hold)
    assert "## Holdout" in hmd and "## Conditions" not in hmd
