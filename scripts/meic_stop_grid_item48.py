"""Item 48, the stop of MEIC's live rule (docs/edge-hypotheses-plan.md): `pm-200` entering once a day,
14:00-14:30, with its per-side stop at 1.8, 1.9, 2.0 and 2.2 times the whole credit, each priced three
ways -- as the loop fills it today, and as a stop-limit resting at the broker at both of its fill
bounds -- against the same rule held to settlement.

    python scripts/meic_stop_grid_item48.py --window design [--workers N] [--json PATH]

Runs the backtest's own MEIC replay (`cherrypick.backtest.runner`, MEIC's own engine over the 60 s
vendor store), provisionally because MEIC history has not unlocked. Design window only: `pm-200-1400`'s
holdout dates were seen on 2026-10-19 (the plan's exception), so its evidence has to come from sessions
after 2026-10-12. Writes under the backtest data dir (results/research/item48/), never into the repo:
the inputs are licensed.

**The arms, as declared.** `live-1400` is the registry's `pm-200` with entries 14:00-14:30, which the
30-minute spacing makes one condor a session: the rule live trades, its 2.0x stop filled by the loop.
`loop-<r>` is `live-1400` with `stop_trigger_ratio` r. `rest-<r>` adds `stop_resting` (paper's
`_resting_stop_fill`: the trigger read off the spread's NBBO ask, as the broker's is, filled at the
tick's ask -- the conservative bound); `restbest-<r>` fills at the trigger instead (the optimistic
bound). `hold` turns the per-side stop off. Nothing else differs.

**The verdict**, per arm, the observation one session (a session with no entry counts, at zero): the
paired difference against `live-1400` with its mean above zero and a one-sided t above 2, Holm across
the arms, positive in both time-halves of the window; and the arm's own net mean above zero, still
positive at 1.5x fees. At least 100 sessions. A `restbest-*` pass is an upper bound and decides nothing
alone: a resting stop is adopted on its `rest-*` (conservative) arm. Each model's own ratio pairs
(`<model>-<r>` against `<model>-200`) are reported beside it; they answer "1.9 or 2.0?" within a model.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from cherrypick.backtest import paths, runner
from cherrypick.backtest import settings as _settings
from cherrypick.core import home

sys.path.insert(0, str(Path(__file__).parent))
from cherrypick.core.procs import default_workers
from meic_delta_grid_item23 import judge as _judge_vs  # noqa: E402
from meic_delta_grid_item23 import own, paired, session_rows  # noqa: E402

BASE_ARM = "pm-200"
LIVE = "live-1400"
WINDOW = ("14:00", "14:30")
RATIOS = {"180": 1.8, "190": 1.9, "200": 2.0, "220": 2.2}
MODELS = {
    "loop": {},
    "rest": {"stop_resting": True, "stop_resting_fill": "tick"},
    "restbest": {"stop_resting": True, "stop_resting_fill": "trigger"},
}
HOLD = "hold"
CADENCE = 60


def arm_name(model: str, ratio: str) -> str:
    return f"{model}-{ratio}"


def grid_arms() -> list[str]:
    """Every arm judged against `live-1400`: each model at each ratio, less the one that IS live-1400
    (`loop-200`), and `hold`."""
    names = [arm_name(m, r) for m in MODELS for r in RATIOS]
    return [n for n in names if n != arm_name("loop", "200")] + [HOLD]


def study_registry(registry: dict) -> dict:
    """The machine's MEIC arm registry with `live-1400` and the grid arms added."""
    profiles = dict(registry.get("profiles") or {})
    if not isinstance(profiles.get(BASE_ARM), dict):
        raise SystemExit(f"the MEIC registry has no {BASE_ARM!r} arm to build on")
    live = {k: v for k, v in profiles[BASE_ARM].items() if not k.startswith("_")}
    live.update(enabled=True, paper_entry_window_start=WINDOW[0], entry_window_end=WINDOW[1])
    if float(live.get("stop_trigger_ratio", 0)) != 2.0:
        raise SystemExit(f"{BASE_ARM!r} no longer stops at 2.0x: the grid is declared around it")
    profiles[LIVE] = live
    for model, extra in MODELS.items():
        for ratio, value in RATIOS.items():
            name = arm_name(model, ratio)
            if name != arm_name("loop", "200"):
                profiles[name] = {**live, **extra, "stop_trigger_ratio": value}
    profiles[HOLD] = {**live, "per_side_stop_management": False}
    return {**registry, "profiles": profiles}


def judge(tests: dict[str, dict]) -> None:
    """item 23's rule, keyed by this study's reference instead of `d16`."""
    for v in tests.values():
        v["vs_d16"] = v["vs_live"]
    _judge_vs(tests)
    for v in tests.values():
        v.pop("vs_d16", None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--window", choices=("design",), required=True)
    ap.add_argument("--workers", type=int, default=default_workers())
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    out_dir = paths.results_dir() / "research" / "item48"
    out_dir.mkdir(parents=True, exist_ok=True)
    registry = json.loads((home.config_dir() / "meic.risk.json").read_text(encoding="utf-8"))
    reg_path = out_dir / "meic.risk.json"
    reg_path.write_text(json.dumps(study_registry(registry), indent=1), encoding="utf-8")
    os.environ["MEIC_RISK_CONFIG"] = str(reg_path)

    s = _settings.load(None)
    arms = grid_arms()
    report = runner.run(
        s,
        [LIVE, *arms],
        args.window,
        module="meic",
        cadence=CADENCE,
        provisional=True,
        workers=args.workers,
        log=lambda m: print(m, file=sys.stderr),
    )
    start, end = s["windows"][args.window]
    per_arm = session_rows(report["run_id"])
    live = per_arm.get(LIVE, {})
    tests = {
        a: {
            "own": own(per_arm.get(a, {}), start, end),
            "vs_live": paired(per_arm.get(a, {}), live, start, end),
        }
        for a in arms
    }
    judge(tests)
    within = {}
    for model in MODELS:
        base_name = LIVE if model == "loop" else arm_name(model, "200")
        for ratio in RATIOS:
            name = arm_name(model, ratio)
            if ratio != "200":
                within[f"{name}_vs_{base_name}"] = paired(
                    per_arm.get(name, {}), per_arm.get(base_name, {}), start, end
                )
    doc = {
        "item": 48,
        "window": args.window,
        "span": [start, end],
        "run_id": report["run_id"],
        "provisional": True,
        "refused_sessions": len(report.get("refused") or {}),
        "vix_fallback_sessions": report.get("vix_fallback_sessions"),
        "reference": {LIVE: own(live, start, end)},
        "arms": tests,
        "within_model": within,
        "verdict": "pass"
        if any(v.get("verdict") == "pass" for a, v in tests.items() if not a.startswith("restbest-"))
        else "fail",
    }
    path = Path(args.json) if args.json else out_dir / f"study-{args.window}-{start}-{end}.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"],
                      **{a: v.get("verdict") for a, v in tests.items()}}, indent=1))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
