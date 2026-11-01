"""Item 44, the stabilization gate across wing widths 10 to 40 on MEIC's live rule
(docs/edge-hypotheses-plan.md): item 43's `w10` rule at widths 10-40, each with and without the gate,
over the wide SPX store (`SPX-60s-wide.db`, 40 strikes each side).

    python scripts/meic_wing_width_item44.py --step coverage
    python scripts/meic_wing_width_item44.py --step plan
    python scripts/meic_wing_width_item44.py --step study --window design|holdout [--workers 8]

Runs the backtest's own MEIC replay (`cherrypick.backtest.runner`) over the wide store, provisionally:
MEIC history has not unlocked and the wide store is never validated. Writes under the backtest data dir
(results/research/item44/), never into the repo: the inputs are licensed.

**The coverage step** reads quotes only, no P&L, and comes first: each store's complete sessions per
window, and the share of 14:00 16-delta shorts (put and call) whose 40-point wing is quoted in the same
sample. **The plan step** reads spot only: over the design window, the share of 14:00-14:30 five-minute
intervals on which `multi` (2 bars, ATR) is not STABLE. That share is `w10-skip`'s placebo rate.

**The arms, as declared.** `wN` is item 43's `w10` at width N (10-40 in steps of 5), ungated; `wN-multi`
is `wN` with item 41's `live-1400-multi` gate. `w10-skip` is `w10` refusing ticks at random at the plan
step's rate, seed 44. `live-1400` is item 43's reference.

**The verdicts**, one session one observation (a session with no entry counts at zero).
(a) each `wN-multi` against its own `wN` by item 41's rule, Holm across the seven, and its paired
difference less `w10-skip`'s against `w10` with a mean above zero and a one-sided t above 2.
(b) `w35` and `w40` against `w10` by item 43's rule (net over buying power included), Holm across the
two. In the holdout, (b) counts after a design pass on its paired mean and t; (a) is a consistency
check only. At least 100 sessions.

**Item 43's holdout comes first.** The runner spends the holdout once per arm spec, and a spec does not
name its store, so `w10`-`w30` here are item 43's specs. The holdout refuses until item 43's holdout
result exists, then reads each of those arms from the run that spent it.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

from cherrypick.backtest import paths, results, runner, store
from cherrypick.backtest import settings as _settings
from cherrypick.core import home

ITEM = 44
SCRIPTS = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Item 41's gate and its rule, item 42's wide store and coverage, item 43's arms and statistics: this
# item crosses them, so each is the one definition.
i41 = _load("meic_stabilization_item41")
i42 = _load("meic_be_condor_item42")
i43 = _load("meic_wing_width_item43")

SYMBOL = "SPX"
CADENCE = i43.CADENCE
BASE = i43.BASE  # w10
LIVE = i43.LIVE
WIDTHS = {f"w{n}": n for n in (10, 15, 20, 25, 30, 35, 40)}
STORE_CHECK = ["w10", "w15", "w20", "w25", "w30"]  # item 43's arms, rerun on the wide store
JUDGED_B = ["w35", "w40"]
GATED = {f"{a}-multi": a for a in WIDTHS}  # gated arm -> its own ungated width
GATE = {**i41.GATE, "stabilization_oracle": "multi", "stabilization_bars": 2}
PLACEBO = "w10-skip"
PLACEBO_SEED = 44
SHORT_DELTA = 0.16
WIDEST = 40
HOURS = (14,)


def out_dir() -> Path:
    d = paths.results_dir() / "research" / f"item{ITEM}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def study_registry(registry: dict, placebo_rate: float) -> dict:
    """The machine's MEIC arm registry with item 44's arms added, built on item 43's."""
    reg = i43.study_registry(registry)
    profiles = {k: v for k, v in reg["profiles"].items() if k not in i43.REPORTED}
    for name, width in WIDTHS.items():
        profiles[name] = {**profiles[BASE], "wing_width_points": width}
    for gated, width_arm in GATED.items():
        profiles[gated] = {**profiles[width_arm], **GATE}
    profiles[PLACEBO] = {
        **profiles[BASE],
        "stabilization_placebo_rate": placebo_rate,
        "stabilization_placebo_seed": PLACEBO_SEED,
    }
    return {**reg, "profiles": profiles}


def _read(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# ── coverage and plan steps ──────────────────────────────────────────────────


def coverage(settings: dict) -> dict:
    doc = {
        "item": ITEM,
        "step": "coverage",
        "store": str(store.db_path(i42.wide(settings), SYMBOL, CADENCE)),
        **i42.reach(settings, HOURS, SHORT_DELTA, WIDEST),
        "note": f"share of {HOURS[0]}:00 {SHORT_DELTA:g}-delta shorts whose {WIDEST}-point wing is quoted "
        "in the same sample; quotes only, no P&L",
    }
    (out_dir() / "coverage.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


def plan(settings: dict) -> dict:
    why = i42.coverage_refusal(_read(out_dir() / "coverage.json"), "design")
    if why:
        raise SystemExit(why)
    start, end = settings["windows"]["design"]
    days, stable, refused = i41.refusals(i42.wide(settings), i43.WINDOW)
    total = stable + refused
    doc = {
        "item": ITEM,
        "step": "plan",
        "span": [start, end],
        "sessions": len(days),
        "intervals": total,
        "refusal_rate": round(refused / total, 4) if total else None,
        "note": f"share of {i43.WINDOW[0]}-{i43.WINDOW[1]} five-minute intervals on which multi "
        "(2 bars, ATR) is not STABLE, over the wide store; spot only, no P&L",
    }
    (out_dir() / "plan.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


# ── study step ───────────────────────────────────────────────────────────────


def placebo_margin(gated: dict, own_width: dict, skip: dict, base: dict, start: str, end: str) -> dict:
    """Per session, the gated arm's gain over its own width less the placebo's over `w10`."""
    days = set(gated) & set(own_width) & set(skip) & set(base)
    mine = {d: {"net": gated[d]["net"] - own_width[d]["net"]} for d in days}
    theirs = {d: {"net": skip[d]["net"] - base[d]["net"]} for d in days}
    return i41.paired(mine, theirs, start, end)


def judge_a(tests: dict[str, dict], holdout: bool) -> None:
    """Item 41's rule per gated arm (Holm across the arms given), then the placebo bar."""
    i41.judge(tests, holdout)
    for v in tests.values():
        if "checks" not in v:
            continue
        m = v["vs_placebo"]
        v["checks"]["beats_placebo"] = (m["mean"] or 0) > 0 and (m["t"] or 0) > i41.T_BAR
        v["verdict"] = "pass" if all(v["checks"].values()) else "fail"


def judge_b(tests: dict[str, dict], holdout: bool) -> None:
    """Item 43's rule against `w10` (Holm across the arms given); the holdout reads the paired mean
    and t only, and counts after a design pass."""
    if not holdout:
        i43.judge(tests)
        return
    for v in tests.values():
        d = v["vs_w10"]
        if d["n"] < i43.MIN_SESSIONS:
            v["verdict"], v["why"] = "no verdict", f"{d['n']} sessions < {i43.MIN_SESSIONS}"
            continue
        v["checks"] = {
            "paired_mean_positive": (d["mean"] or 0) > 0,
            "paired_t_above_bar": (d["t"] or 0) > i43.T_BAR,
        }
        v["verdict"] = "pass" if all(v["checks"].values()) else "fail"


def spent_runs(arms: list[str]) -> dict[str, str]:
    """The arms whose holdout is already spent under this registry's spec, with the run that spent it."""
    mod = runner.MODULES["meic"]
    config, _ = mod.load()
    conn = results.connect()
    try:
        out = {}
        for arm in arms:
            h = runner.spec_hash(mod.spec_for(config, arm, cadence=CADENCE))
            row = conn.execute(
                "SELECT run_id FROM holdout_spent WHERE module = ? AND spec_hash = ?", ("meic", h)
            ).fetchone()
            if row:
                out[arm] = row["run_id"]
        return out
    finally:
        conn.close()


def item43_result(window: str) -> dict | None:
    files = sorted((paths.results_dir() / "research" / "item43").glob(f"study-{window}-*.json"))
    return _read(files[-1]) if files else None


def holdout_refusal(item43_holdout: dict | None) -> str | None:
    if item43_holdout is None:
        return (
            "item 43's holdout has not run: it spends w10-w30's holdout, so it reads first and this run "
            "borrows those arms from it"
        )
    return None


def store_check(ours: dict[str, dict], item43_design: dict | None, start: str, end: str) -> dict:
    """`w10`-`w30` on the wide store against item 43's same arms on the plain one, per arm."""
    if item43_design is None or not item43_design.get("run_id"):
        return {"note": "item 43's design result is not on disk; nothing to compare"}
    theirs = i43.session_rows(item43_design["run_id"])
    out: dict = {"item43_run_id": item43_design["run_id"]}
    for arm in STORE_CHECK:
        d = i41.paired(ours.get(arm, {}), theirs.get(arm, {}), start, end)
        d["beyond_noise"] = d["t"] is not None and abs(d["t"]) > i41.T_BAR
        out[arm] = d
    return out


def study(settings: dict, window: str, workers: int) -> dict:
    i41.require_gate()
    why = i42.coverage_refusal(_read(out_dir() / "coverage.json"), window)
    if why:
        raise SystemExit(why)
    plan_doc = _read(out_dir() / "plan.json")
    if plan_doc is None:
        raise SystemExit("run --step plan first: the placebo's rate comes from it")
    holdout = window == "holdout"
    if holdout:
        why = holdout_refusal(item43_result("holdout"))
        if why:
            raise SystemExit(why)
    rate = plan_doc["refusal_rate"]
    registry = json.loads((home.config_dir() / "meic.risk.json").read_text(encoding="utf-8"))
    reg_path = out_dir() / "meic.risk.json"
    reg_path.write_text(json.dumps(study_registry(registry, rate), indent=1), encoding="utf-8")
    os.environ["MEIC_RISK_CONFIG"] = str(reg_path)

    borrowed = spent_runs(STORE_CHECK) if holdout else {}
    arms = ([] if holdout else [LIVE]) + [a for a in WIDTHS if a not in borrowed] + [*GATED, PLACEBO]
    report = runner.run(
        i42.wide(settings),
        arms,
        window,
        module="meic",
        cadence=CADENCE,
        provisional=True,
        spend_holdout=holdout,
        unvalidated_holdout=holdout,
        workers=workers,
        log=lambda m: print(m, file=sys.stderr),
    )
    start, end = settings["windows"][window]
    per_arm = i43.session_rows(report["run_id"])
    rows = i43.ledger_rows(paths.results_dir() / "runs" / report["run_id"])
    for arm, run_id in borrowed.items():
        per_arm[arm] = i43.session_rows(run_id).get(arm, {})
        rows += [r for r in i43.ledger_rows(paths.results_dir() / "runs" / run_id) if r["arm"] == arm]
    risk = i43.risk_by_session(rows)
    live, live_run = i43.item23_live_holdout() if holdout else (per_arm.get(LIVE, {}), report["run_id"])
    base, skip = per_arm.get(BASE, {}), per_arm.get(PLACEBO, {})

    tests_a = {}
    for gated, width_arm in GATED.items():
        mine, theirs = per_arm.get(gated, {}), per_arm.get(width_arm, {})
        tests_a[gated] = {
            "base": width_arm,
            "own": i41.own(mine, start, end),
            "vs_base": i41.paired(mine, theirs, start, end),
            "vs_placebo": placebo_margin(mine, theirs, skip, base, start, end),
        }
    judge_a(tests_a, holdout)
    tests_b = {}
    entered_base = {d for d, x in base.items() if x["trades"]}
    for arm in JUDGED_B:
        mine = per_arm.get(arm, {})
        both = sorted(entered_base & {d for d, x in mine.items() if x["trades"]})
        tests_b[arm] = {
            "own": i43.own(mine, start, end),
            "vs_w10": i43.paired(mine, base, start, end),
            "vs_w10_both_entered": i43.paired(mine, base, start, end, days=both),
            "per_risk": i43.per_risk(mine, base, risk, arm),
        }
    judge_b(tests_b, holdout)

    def verdict(tests):
        return "pass" if any(v.get("verdict") == "pass" for v in tests.values()) else "fail"

    doc = {
        "item": ITEM,
        "window": window,
        "span": [start, end],
        "store": str(store.db_path(i42.wide(settings), SYMBOL, CADENCE)),
        "run_id": report["run_id"],
        "borrowed_from_item43": borrowed,
        "live_1400_run_id": live_run,
        "provisional": True,
        "placebo_rate": rate,
        "refused_sessions": len(report.get("refused") or {}),
        "vix_fallback_sessions": report.get("vix_fallback_sessions"),
        "coverage": i43.coverage(rows, per_arm, [*WIDTHS, *GATED, PLACEBO]),
        "reference": {
            BASE: i43.own(base, start, end),
            LIVE: i43.own(live, start, end),
            f"{BASE}_vs_{LIVE}": i43.paired(base, live, start, end),
            PLACEBO: i41.own(skip, start, end),
            f"{PLACEBO}_vs_{BASE}": i41.paired(skip, base, start, end),
            **{a: i43.own(per_arm.get(a, {}), start, end) for a in STORE_CHECK if a != BASE},
        },
        "store_check": None if holdout else store_check(per_arm, item43_result("design"), start, end),
        "gate_by_width": tests_a,
        "wide_widths": tests_b,
        # In the holdout the gate's question is a consistency check: item 41 reads these dates first.
        "verdict": {
            "a": verdict(tests_a) if not holdout else f"consistency: {verdict(tests_a)}",
            "b": verdict(tests_b),
        },
    }
    path = out_dir() / f"study-{window}-{start}-{end}.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    return {
        "written": str(path),
        "verdict": doc["verdict"],
        **{a: v.get("verdict") for a, v in {**tests_a, **tests_b}.items()},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--step", required=True, choices=("coverage", "plan", "study"))
    ap.add_argument("--window", choices=("design", "holdout"), default="design")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args(argv)
    s = _settings.load(None)
    steps = {"coverage": lambda: coverage(s), "plan": lambda: plan(s),
             "study": lambda: study(s, args.window, args.workers)}  # fmt: skip
    print(json.dumps(steps[args.step](), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
