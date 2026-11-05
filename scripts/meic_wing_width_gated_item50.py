"""Item 50, item 43's wider wings with live's gates on (docs/edge-hypotheses-plan.md): `pm-200`
entering once a day, 14:00-14:30, at 15 and 20 points wide with its credit floor and call-delta
ceiling as live has them, each paired with the same rule at its own 10.

    python scripts/meic_wing_width_gated_item50.py --window design|holdout [--workers 8] [--json PATH]

Runs the backtest's own MEIC replay (`cherrypick.backtest.runner`, MEIC's own engine over the 60 s
vendor store), provisionally because MEIC history has not unlocked. The holdout is spent by this run,
once per arm (`unvalidated_holdout`). Writes under the backtest data dir (results/research/item50/),
never into the repo: the inputs are licensed.

**The arms, as declared.** `live-1400` is item 43's: the registry's `pm-200` with entries 14:00-14:30,
gates and all, and the base every arm is judged against. `g15` and `g20` are `live-1400` with
`wing_width_points` changed; nothing else differs, so the credit floor, a share of the width, asks
more credit of them, as it would live. Item 43's ungated `w15` and `w20` are read from its own runs and
reported beside them, never judged: `g` against `w` is what the gates cost at that width.

**The verdict**, per arm, the observation one session (a session with no entry counts, at zero): the
paired difference against `live-1400` with its mean above zero and a one-sided t above 2, Holm across
the two arms, positive in both time-halves of the window; the arm's own net mean above zero, still
positive at 1.5x fees; and its net over buying power at least `live-1400`'s over the same sessions.
At least 100 sessions. The arithmetic is item 43's (`own`, `paired`, the ledger readers), loaded from
its script so the two items cannot drift apart.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

from cherrypick.backtest import grid, paths, runner
from cherrypick.backtest import settings as _settings
from cherrypick.core import home
from cherrypick.core import stats as core_stats

_spec = importlib.util.spec_from_file_location(
    "meic_wing_width_item43", Path(__file__).with_name("meic_wing_width_item43.py")
)
i43 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(i43)

BASE_ARM = i43.BASE_ARM
BASE = i43.LIVE  # "live-1400"
BASE_WIDTH = 10
GRID = {"g15": 15, "g20": 20}
#: Item 43's ungated arm at each judged width: reported beside it, read from item 43's own runs.
UNGATED_TWIN = {"g15": "w15", "g20": "w20"}
MIN_SESSIONS = i43.MIN_SESSIONS
T_BAR = i43.T_BAR
ALPHA = i43.ALPHA


def study_registry(registry: dict) -> dict:
    """The machine's MEIC arm registry with `live-1400` and the gated grid arms added."""
    profiles = dict(registry.get("profiles") or {})
    if not isinstance(profiles.get(BASE_ARM), dict):
        raise SystemExit(f"the MEIC registry has no {BASE_ARM!r} arm to build on")
    live = {k: v for k, v in profiles[BASE_ARM].items() if not k.startswith("_")}
    live.update(enabled=True, paper_entry_window_start=i43.WINDOW[0], entry_window_end=i43.WINDOW[1])
    profiles[BASE] = live
    for name, width in GRID.items():
        profiles[name] = {**live, "wing_width_points": width}
    return {**registry, "profiles": profiles}


def per_risk(arm: dict, base: dict, risk: dict, name: str) -> dict:
    """Net over buying power, the arm's and `live-1400`'s, over the sessions both ran."""
    days = sorted(set(arm) & set(base))

    def ratio(sessions: dict, arm_name: str) -> float | None:
        held = sum(risk.get(arm_name, {}).get(d, 0.0) for d in days)
        return round(sum(sessions[d]["net"] for d in days) / held, 5) if held > 0 else None

    mine, theirs = ratio(arm, name), ratio(base, BASE)
    at_least = mine is not None and theirs is not None and mine >= theirs
    return {"arm": mine, BASE: theirs, "at_least_base": at_least}


def judge(tests: dict[str, dict]) -> None:
    holm = core_stats.holm({k: v["vs_base"]["p"] for k, v in tests.items()}, alpha=ALPHA)
    for k, v in tests.items():
        d, o = v["vs_base"], v["own"]
        v["holm_ok"] = holm[k]
        if d["n"] < MIN_SESSIONS:
            v["verdict"] = "no verdict"
            v["why"] = f"{d['n']} sessions < {MIN_SESSIONS}"
            continue
        checks = {
            "paired_mean_positive": (d["mean"] or 0) > 0,
            "paired_t_above_bar": (d["t"] or 0) > T_BAR,
            "holm": v["holm_ok"],
            "paired_both_halves_positive": all((x or 0) > 0 for x in d["halves"].values()),
            "own_mean_positive": (o["mean_net"] or 0) > 0,
            "own_survives_1_5x_fees": o["cost_stress"]["survives"],
            "net_per_risk_at_least_base": v["per_risk"]["at_least_base"],
        }
        v["checks"] = checks
        v["verdict"] = "pass" if all(checks.values()) else "fail"


def run_arms(holdout: bool) -> list[str]:
    """The arms one run replays. Item 23 spent `live-1400`'s holdout, so there it is read from item 23's
    recorded sessions instead (item 43's own reader)."""
    return ([] if holdout else [BASE]) + list(GRID)


def item43_sessions(window: str) -> tuple[dict[str, dict], str | None]:
    """Item 43's recorded sessions for its ungated twins in `window`, with that run's id."""
    files = sorted((paths.results_dir() / "research" / "item43").glob(f"study-{window}-*.json"))
    files = [f for f in files if not f.name.endswith(".version.json")]
    if not files:
        return {}, None
    run_id = json.loads(files[-1].read_text(encoding="utf-8")).get("run_id")
    rows = i43.session_rows(run_id) if run_id else {}
    return {a: rows.get(a, {}) for a in UNGATED_TWIN.values()}, run_id


def coverage(rows: list[dict], per_arm: dict, base: dict, arms: list[str]) -> dict:
    """Per arm: sessions entered, sessions `live-1400` entered and the arm did not (mostly the credit
    floor asking more of a wider wing, or the store's wing window; the ledger cannot tell them
    apart), and entries with an unquoted tick."""
    entered_base = {d for d, s in base.items() if s["trades"]}
    unmarked: dict[str, int] = {}
    for r in rows:
        if int(r["unmarked_iterations"] or 0) > 0:
            unmarked[r["arm"]] = unmarked.get(r["arm"], 0) + 1
    out = {}
    for a in arms:
        entered = {d for d, s in per_arm.get(a, {}).items() if s["trades"]}
        out[a] = {
            "sessions_entered": len(entered),
            "base_entered_arm_did_not": len(entered_base - entered),
            "entries_with_unquoted_ticks": unmarked.get(a, 0),
        }
    return out


def grid_block(doc: dict) -> dict:
    """The settings side by side for the studies board: `live-1400` is the live width."""
    return grid.block(doc, "wing width (gated)", {BASE: BASE_WIDTH, **GRID}, BASE,
                      sessions=grid.run_sessions(doc.get("run_id")))  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--window", choices=("design", "holdout"), required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    out_dir = paths.results_dir() / "research" / "item50"
    out_dir.mkdir(parents=True, exist_ok=True)
    registry = json.loads((home.config_dir() / "meic.risk.json").read_text(encoding="utf-8"))
    reg_path = out_dir / "meic.risk.json"
    reg_path.write_text(json.dumps(study_registry(registry), indent=1), encoding="utf-8")
    os.environ["MEIC_RISK_CONFIG"] = str(reg_path)

    s = _settings.load(None)
    holdout = args.window == "holdout"
    arms = run_arms(holdout)
    report = runner.run(
        s,
        arms,
        args.window,
        module="meic",
        cadence=i43.CADENCE,
        provisional=True,
        spend_holdout=holdout,
        unvalidated_holdout=holdout,
        workers=args.workers,
        log=lambda m: print(m, file=sys.stderr),
    )
    start, end = s["windows"][args.window]
    per_arm = i43.session_rows(report["run_id"])
    base, base_run = i43.item23_live_holdout() if holdout else (per_arm.get(BASE, {}), report["run_id"])
    rows = i43.ledger_rows(paths.results_dir() / "runs" / report["run_id"])
    risk = i43.risk_by_session(rows)
    twins, twin_run = item43_sessions(args.window)
    entered_base = {d for d, x in base.items() if x["trades"]}

    tests = {}
    for a in GRID:
        mine = per_arm.get(a, {})
        both = sorted(entered_base & {d for d, x in mine.items() if x["trades"]})
        twin = twins.get(UNGATED_TWIN[a], {})
        tests[a] = {
            "own": i43.own(mine, start, end),
            "vs_base": i43.paired(mine, base, start, end),
            "vs_base_both_entered": i43.paired(mine, base, start, end, days=both),
            "per_risk": per_risk(mine, base, risk, a),
            # Reported only: the gated arm less item 43's ungated one at the same width.
            "vs_ungated_twin": i43.paired(mine, twin, start, end) if twin else None,
        }
    judge(tests)
    doc = {
        "item": 50,
        "window": args.window,
        "span": [start, end],
        "run_id": report["run_id"],
        "live_1400_run_id": base_run,
        "item43_run_id": twin_run,
        "provisional": True,
        "refused_sessions": len(report.get("refused") or {}),
        "vix_fallback_sessions": report.get("vix_fallback_sessions"),
        "coverage": coverage(rows, per_arm, base, list(GRID)),
        "reference": {BASE: i43.own(base, start, end)},
        "arms": tests,
        "verdict": "pass" if any(v.get("verdict") == "pass" for v in tests.values()) else "fail",
    }
    doc["grid"] = grid_block(doc)
    path = Path(args.json) if args.json else out_dir / f"study-{args.window}-{start}-{end}.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"],
                      **{a: v.get("verdict") for a, v in tests.items()}}, indent=1))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
