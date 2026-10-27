"""Item 21, legged flies with a resting entry limit (docs/edge-hypotheses-plan.md): control's legged SPX
0DTE flies, with the entry resting above mid and filled only by the market reaching it.

    python scripts/flies_entry_limit_item21.py --window design|holdout [--workers 8] [--json PATH]

Runs the backtest's own flies replay (`cherrypick.backtest.runner`, flies' own rules over the 60 s
vendor store), provisionally because flies history cannot unlock: its validation stopped at five
sessions when paper did. The holdout is spent by this run, once (`unvalidated_holdout`). Writes under
the backtest data dir (results/research/item21/), never into the repo: the inputs are licensed.

**The arms, as declared.** `control` and `range-high` are the machine's own arms, run as references.
`limit-000`, `limit-005` and `limit-010` are control with the entry resting at the spread's mid plus
0.00, 0.05 or 0.10, floored to the tick, filled at the limit on the first later tick whose mid reaches
it within 120 s, else lapsed and re-planned (`engine.entry_limit_settings`). The `-rh` twins are the
same on `range-high`. Completion, sizing and everything else are control's.

**The verdict**, per limit arm, the observation one session's summed net: the mean above zero with a
one-sided t above 2, Holm across the six limit arms, both time-halves of the window positive, still
positive at 1.5x fees, and at least 100 sessions. The same arm's paired difference against its base
(control, or range-high for a twin) is reported, never judged: beating a losing control is not an
edge.
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

from cherrypick.backtest import paths, results, runner
from cherrypick.backtest import settings as _settings
from cherrypick.core import home, robustness
from cherrypick.core import stats as core_stats

OFFSETS = {"000": 0.0, "005": 0.05, "010": 0.10}
WINDOW_SECONDS = 120
BASIS = "mid"
CADENCE = 60
MIN_SESSIONS = 100
T_BAR = 2.0
ALPHA = 0.05
PASS_STRESS = 1.5


def limit_arms() -> dict[str, str]:
    """Each limit arm and the machine arm it is built on."""
    out = {}
    for tag in OFFSETS:
        out[f"limit-{tag}"] = "control"
        out[f"limit-{tag}-rh"] = "range-high"
    return out


def study_config(base: dict) -> dict:
    """The machine's flies config with the six limit arms added, each its base arm plus the limit."""
    arms = dict(base.get("arms") or {})
    for name in ("control", "range-high"):
        if not isinstance(arms.get(name), dict):
            raise SystemExit(f"the flies config has no {name!r} arm to build on")
    for name, source in limit_arms().items():
        offset = OFFSETS[name.split("-")[1]]
        arms[name] = {
            **{k: v for k, v in arms[source].items() if not k.startswith("_")},
            "enabled": True,
            "entry_limit": {"offset": offset, "window_seconds": WINDOW_SECONDS, "basis": BASIS},
        }
    return {**base, "arms": arms}


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


def score(sessions: dict[str, dict], start: str, end: str) -> dict:
    """One arm's sessions ({day: {net, fees, trades}}) against the declared bars' measures."""
    days = sorted(sessions)
    nets = [sessions[d]["net"] for d in days]
    fees = [sessions[d]["fees"] for d in days]
    mid = midpoint(start, end)
    halves = {
        "first": [sessions[d]["net"] for d in days if d < mid],
        "second": [sessions[d]["net"] for d in days if d >= mid],
    }
    t = t_stat(nets)
    out = {
        "n": len(nets),
        "trades": sum(sessions[d]["trades"] for d in days),
        "mean_net": round(statistics.fmean(nets), 2) if nets else None,
        "t": None if t is None else round(t, 3),
        "p": p_one(t, len(nets)),
        "halves": {h: round(statistics.fmean(v), 2) if v else None for h, v in halves.items()},
        "halves_n": {h: len(v) for h, v in halves.items()},
        "cost_stress": robustness.cost_stress(nets, fees, pass_stress=PASS_STRESS),
        "win_rate": round(sum(x > 0 for x in nets) / len(nets), 3) if nets else None,
    }
    if len(nets) < MIN_SESSIONS:
        out["verdict"] = "no verdict"
        out["why"] = f"{len(nets)} sessions < {MIN_SESSIONS}"
    return out


def judge(tests: dict[str, dict]) -> None:
    holm = core_stats.holm({k: v["p"] for k, v in tests.items()}, alpha=ALPHA)
    for k, v in tests.items():
        v["holm_ok"] = holm[k]
        if v.get("verdict") == "no verdict":
            continue
        checks = {
            "mean_positive": (v["mean_net"] or 0) > 0,
            "t_above_bar": (v["t"] or 0) > T_BAR,
            "holm": v["holm_ok"],
            "both_halves_positive": all((x or 0) > 0 for x in v["halves"].values()),
            "survives_1_5x_fees": v["cost_stress"]["survives"],
        }
        v["checks"] = checks
        v["verdict"] = "pass" if all(checks.values()) else "fail"


def paired(arm: dict[str, dict], base: dict[str, dict]) -> dict:
    """The arm's session net less its base's, over the sessions both ran. Description only."""
    diffs = [arm[d]["net"] - base[d]["net"] for d in sorted(set(arm) & set(base))]
    t = t_stat(diffs)
    return {
        "n": len(diffs),
        "mean": round(statistics.fmean(diffs), 2) if diffs else None,
        "t": None if t is None else round(t, 3),
    }


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--window", required=True, choices=("design", "holdout"))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    out_dir = paths.results_dir() / "research" / "item21"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = json.loads(home.config_path("flies").read_text(encoding="utf-8"))
    cfg_path = out_dir / "flies-config.json"
    cfg_path.write_text(json.dumps(study_config(base), indent=1), encoding="utf-8")
    os.environ["FLIES_CONFIG"] = str(cfg_path)

    s = _settings.load(None)
    arms = ["range-high", *limit_arms()]
    holdout = args.window == "holdout"
    report = runner.run(
        s,
        arms,
        args.window,
        cadence=CADENCE,
        provisional=True,
        spend_holdout=holdout,
        unvalidated_holdout=holdout,
        workers=args.workers,
        log=lambda m: print(m, file=sys.stderr),
    )
    start, end = s["windows"][args.window]
    per_arm = session_rows(report["run_id"])
    tests = {a: score(per_arm.get(a, {}), start, end) for a in limit_arms()}
    judge(tests)
    doc = {
        "item": 21,
        "window": args.window,
        "span": [start, end],
        "run_id": report["run_id"],
        "provisional": True,
        "refused_sessions": len(report.get("refused") or {}),
        "references": {a: score(per_arm.get(a, {}), start, end) for a in ("control", "range-high")},
        "arms": tests,
        "vs_base": {a: paired(per_arm.get(a, {}), per_arm.get(src, {})) for a, src in limit_arms().items()},
        "verdict": "pass" if any(v.get("verdict") == "pass" for v in tests.values()) else "fail",
    }
    path = Path(args.json) if args.json else out_dir / f"study-{args.window}-{start}-{end}.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"],
                      **{a: v.get("verdict") for a, v in tests.items()}}, indent=1))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
