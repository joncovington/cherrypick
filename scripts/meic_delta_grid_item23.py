"""Item 23, the short delta of MEIC's live rule (docs/edge-hypotheses-plan.md): `pm-200` entering once
a day, 14:00-14:30, at 12, 20, 25 and 30 delta, each paired with the same rule at its own 16.

    python scripts/meic_delta_grid_item23.py --window design|holdout [--workers 8] [--json PATH]
    python scripts/meic_delta_grid_item23.py --annotate FILE [FILE ...]

Runs the backtest's own MEIC replay (`cherrypick.backtest.runner`, MEIC's own engine over the 60 s
vendor store), provisionally because MEIC history has not unlocked. The holdout is spent by this run,
once (`unvalidated_holdout`). Writes under the backtest data dir (results/research/item23/), never into
the repo: the inputs are licensed.

**The arms, as declared.** `live-1400` is the registry's `pm-200` with entries 14:00-14:30, which the
30-minute spacing makes one condor a session: the rule live trades, gates and all. `d16` is `live-1400`
with no credit floor (the low-IV relief floor too) and no call-delta ceiling. `d12`, `d20`, `d25` and
`d30` are `d16` with `short_delta_target` changed; nothing else differs. `live-1400` is a reference,
never judged; `d16` against it is reported, which is what the gates do at 16.

**The verdict**, per grid arm, the observation one session (a session with no entry counts, at zero):
the paired difference against `d16` with its mean above zero and a one-sided t above 2, Holm across the
four arms, positive in both time-halves of the window; and the arm's own net mean above zero, still
positive at 1.5x fees. At least 100 sessions. Every arm's own net is reported beside it, `d16` included.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from datetime import date
from pathlib import Path

from cherrypick.backtest import grid, paths, results, runner
from cherrypick.backtest import settings as _settings
from cherrypick.core import home, robustness
from cherrypick.core import stats as core_stats

BASE_ARM = "pm-200"
LIVE = "live-1400"
BASE = "d16"
BASE_DELTA = 0.16
GRID = {"d12": 0.12, "d20": 0.20, "d25": 0.25, "d30": 0.30}
WINDOW = ("14:00", "14:30")
# No credit floor (the low-IV relief floor too) and no call-delta ceiling: the delta is the only
# difference between the arms, and no gate refuses one band more than another.
UNGATED = {
    "min_credit_pct_of_width": 0.0,
    "low_iv_min_credit_pct_of_width": 0.0,
    "max_call_delta_entry": 1.0,
    "max_call_delta_entry_open_volatile": 1.0,
    "max_call_delta_entry_late": 1.0,
}
CADENCE = 60
MIN_SESSIONS = 100
T_BAR = 2.0
ALPHA = 0.05
PASS_STRESS = 1.5


def study_registry(registry: dict) -> dict:
    """The machine's MEIC arm registry with `live-1400`, `d16` and the four grid arms added."""
    profiles = dict(registry.get("profiles") or {})
    if not isinstance(profiles.get(BASE_ARM), dict):
        raise SystemExit(f"the MEIC registry has no {BASE_ARM!r} arm to build on")
    live = {k: v for k, v in profiles[BASE_ARM].items() if not k.startswith("_")}
    live.update(enabled=True, paper_entry_window_start=WINDOW[0], entry_window_end=WINDOW[1])
    profiles[LIVE] = live
    profiles[BASE] = {**live, **UNGATED}
    for name, target in GRID.items():
        profiles[name] = {**profiles[BASE], "short_delta_target": target}
    return {**registry, "profiles": profiles}


def t_stat(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    sd = statistics.stdev(xs)
    return statistics.fmean(xs) / (sd / math.sqrt(len(xs))) if sd > 0 else None


def p_one(t: float | None, n: int) -> float:
    return 1.0 if t is None or n < 2 else 1.0 - core_stats.t_cdf(t, n - 1)


def midpoint(start: str, end: str) -> str:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return date.fromordinal((a.toordinal() + b.toordinal()) // 2).isoformat()


def own(sessions: dict[str, dict], start: str, end: str) -> dict:
    """One arm's own sessions ({day: {net, fees, trades}}): its net, halves and the fee stress."""
    days = sorted(sessions)
    nets = [sessions[d]["net"] for d in days]
    fees = [sessions[d]["fees"] for d in days]
    mid = midpoint(start, end)
    t = t_stat(nets)
    return {
        "n": len(nets),
        "trades": sum(sessions[d]["trades"] for d in days),
        "mean_net": round(statistics.fmean(nets), 2) if nets else None,
        "t": None if t is None else round(t, 3),
        "halves": {
            "first": _mean([sessions[d]["net"] for d in days if d < mid]),
            "second": _mean([sessions[d]["net"] for d in days if d >= mid]),
        },
        "cost_stress": robustness.cost_stress(nets, fees, pass_stress=PASS_STRESS),
        "win_rate": round(sum(x > 0 for x in nets) / len(nets), 3) if nets else None,
    }


def paired(arm: dict[str, dict], base: dict[str, dict], start: str, end: str) -> dict:
    """The arm's session net less `d16`'s, over the sessions both ran."""
    days = sorted(set(arm) & set(base))
    diffs = {d: arm[d]["net"] - base[d]["net"] for d in days}
    xs = list(diffs.values())
    mid = midpoint(start, end)
    t = t_stat(xs)
    return {
        "n": len(xs),
        "mean": _mean(xs),
        "t": None if t is None else round(t, 3),
        "p": p_one(t, len(xs)),
        "halves": {
            "first": _mean([v for d, v in diffs.items() if d < mid]),
            "second": _mean([v for d, v in diffs.items() if d >= mid]),
        },
    }


def _mean(xs: list[float]) -> float | None:
    return round(statistics.fmean(xs), 2) if xs else None


def judge(tests: dict[str, dict]) -> None:
    holm = core_stats.holm({k: v["vs_d16"]["p"] for k, v in tests.items()}, alpha=ALPHA)
    for k, v in tests.items():
        d, o = v["vs_d16"], v["own"]
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
        }
        v["checks"] = checks
        v["verdict"] = "pass" if all(checks.values()) else "fail"


def session_rows(run_id: str) -> dict[str, dict[str, dict]]:
    conn = results.connect()
    try:
        out: dict[str, dict[str, dict]] = {}
        for arm, day, net, fees, trades in conn.execute(
            "SELECT arm, session, net, fees, trades FROM session_results WHERE run_id = ?", (run_id,)
        ):
            out.setdefault(arm, {})[day] = {"net": net, "fees": fees or 0.0, "trades": trades}
        return out
    finally:
        conn.close()


def grid_block(doc: dict) -> dict:
    """The settings side by side for the studies board: `d16` is the live delta."""
    return grid.block(doc, "short delta", {BASE: BASE_DELTA, **GRID}, BASE)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--window", choices=("design", "holdout"))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--json", default=None)
    ap.add_argument(
        "--annotate", nargs="+", metavar="FILE", help="add the grid block to result files; no replay"
    )
    args = ap.parse_args(argv)
    if args.annotate:
        print(json.dumps({"annotated": grid.annotate(args.annotate, grid_block)}, indent=1))
        return 0
    if not args.window:
        ap.error("--window is required unless --annotate")

    out_dir = paths.results_dir() / "research" / "item23"
    out_dir.mkdir(parents=True, exist_ok=True)
    registry = json.loads((home.config_dir() / "meic.risk.json").read_text(encoding="utf-8"))
    reg_path = out_dir / "meic.risk.json"
    reg_path.write_text(json.dumps(study_registry(registry), indent=1), encoding="utf-8")
    os.environ["MEIC_RISK_CONFIG"] = str(reg_path)

    s = _settings.load(None)
    holdout = args.window == "holdout"
    report = runner.run(
        s,
        [LIVE, BASE, *GRID],
        args.window,
        module="meic",
        cadence=CADENCE,
        provisional=True,
        spend_holdout=holdout,
        unvalidated_holdout=holdout,
        workers=args.workers,
        log=lambda m: print(m, file=sys.stderr),
    )
    start, end = s["windows"][args.window]
    per_arm = session_rows(report["run_id"])
    base = per_arm.get(BASE, {})
    tests = {
        a: {
            "own": own(per_arm.get(a, {}), start, end),
            "vs_d16": paired(per_arm.get(a, {}), base, start, end),
        }
        for a in GRID
    }
    judge(tests)
    doc = {
        "item": 23,
        "window": args.window,
        "span": [start, end],
        "run_id": report["run_id"],
        "provisional": True,
        "refused_sessions": len(report.get("refused") or {}),
        "vix_fallback_sessions": report.get("vix_fallback_sessions"),
        "reference": {
            BASE: own(base, start, end),
            LIVE: own(per_arm.get(LIVE, {}), start, end),
            f"{BASE}_vs_{LIVE}": paired(base, per_arm.get(LIVE, {}), start, end),
        },
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
