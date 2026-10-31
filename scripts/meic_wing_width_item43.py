"""Item 43, the wing width of MEIC's live rule (docs/edge-hypotheses-plan.md): `pm-200` entering once
a day, 14:00-14:30, at 15, 20, 25 and 30 points wide, each paired with the same rule at its own 10.

    python scripts/meic_wing_width_item43.py --window design|holdout [--workers 8] [--json PATH]

Runs the backtest's own MEIC replay (`cherrypick.backtest.runner`, MEIC's own engine over the 60 s
vendor store), provisionally because MEIC history has not unlocked. The holdout is spent by this run,
once (`unvalidated_holdout`). Writes under the backtest data dir (results/research/item43/), never into
the repo: the inputs are licensed.

**The arms, as declared.** `live-1400` is the registry's `pm-200` with entries 14:00-14:30, which the
30-minute spacing makes one condor a session: the rule live trades, gates and all. `w10` is `live-1400`
with no credit floor (the low-IV relief floor too) and no call-delta ceiling. `w15`, `w20`, `w25` and
`w30` are `w10` with `wing_width_points` changed; nothing else differs. `w5` is the same at 5, reported
and never judged (the 2026-10-19 run already answered it). `live-1400` is a reference, never judged;
`w10` against it is reported, which is what the gates do at 10.

**The verdict**, per grid arm, the observation one session (a session with no entry counts, at zero):
the paired difference against `w10` with its mean above zero and a one-sided t above 2, Holm across the
four arms, positive in both time-halves of the window; the arm's own net mean above zero, still
positive at 1.5x fees; and its net over buying power at least `w10`'s over the same sessions, so that
paying more by risking more does not pass. At least 100 sessions.

**Store coverage.** The store keeps about 20 strikes either side of spot, so a wing beyond it is MEIC's
own "no candidate" and a leg that leaves it while open holds `quotes_unavailable`. Both are counted per
arm from the run's ledgers and reported beside the verdict, with the paired difference over the
sessions both arms entered.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import statistics
import sys
from datetime import date
from pathlib import Path

from cherrypick.backtest import paths, results, runner
from cherrypick.backtest import settings as _settings
from cherrypick.core import home, robustness
from cherrypick.core import stats as core_stats

BASE_ARM = "pm-200"
LIVE = "live-1400"
BASE = "w10"
GRID = {"w15": 15, "w20": 20, "w25": 25, "w30": 30}
REPORTED = {"w5": 5}
WINDOW = ("14:00", "14:30")
# No credit floor (the low-IV relief floor too) and no call-delta ceiling: the floor is a share of the
# width, so with it on a wider wing would need more credit, and the width would not be the only
# difference between the arms.
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
    """The machine's MEIC arm registry with `live-1400`, `w10`, the grid arms and `w5` added."""
    profiles = dict(registry.get("profiles") or {})
    if not isinstance(profiles.get(BASE_ARM), dict):
        raise SystemExit(f"the MEIC registry has no {BASE_ARM!r} arm to build on")
    live = {k: v for k, v in profiles[BASE_ARM].items() if not k.startswith("_")}
    live.update(enabled=True, paper_entry_window_start=WINDOW[0], entry_window_end=WINDOW[1])
    profiles[LIVE] = live
    profiles[BASE] = {**live, **UNGATED, "wing_width_points": 10}
    for name, width in {**GRID, **REPORTED}.items():
        profiles[name] = {**profiles[BASE], "wing_width_points": width}
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


def paired(arm: dict[str, dict], base: dict[str, dict], start: str, end: str, days=None) -> dict:
    """The arm's session net less `w10`'s, over the sessions both ran (or the `days` given)."""
    days = sorted(set(arm) & set(base) if days is None else days)
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


def per_risk(arm: dict[str, dict], base: dict[str, dict], risk: dict[str, dict[str, float]],
             name: str) -> dict:  # fmt: skip
    """Net over buying power, the arm's and `w10`'s, over the sessions both ran: summed net over the
    summed `(width - credit) x 100 x quantity` of every entry."""
    days = sorted(set(arm) & set(base))

    def ratio(sessions: dict[str, dict], arm_name: str) -> float | None:
        held = sum(risk.get(arm_name, {}).get(d, 0.0) for d in days)
        return round(sum(sessions[d]["net"] for d in days) / held, 5) if held > 0 else None

    mine, theirs = ratio(arm, name), ratio(base, BASE)
    return {
        "arm": mine,
        BASE: theirs,
        "at_least_w10": mine is not None and theirs is not None and mine >= theirs,
    }


def _mean(xs: list[float]) -> float | None:
    return round(statistics.fmean(xs), 2) if xs else None


def judge(tests: dict[str, dict]) -> None:
    holm = core_stats.holm({k: v["vs_w10"]["p"] for k, v in tests.items()}, alpha=ALPHA)
    for k, v in tests.items():
        d, o = v["vs_w10"], v["own"]
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
            "net_per_risk_at_least_w10": v["per_risk"]["at_least_w10"],
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


def ledger_rows(ledger_dir: Path) -> list[dict]:
    """Every entry the run's per-session ledgers hold: arm, session, width, credit and how many ticks a
    leg went unquoted."""
    out = []
    for db in sorted(ledger_dir.glob("*.db")):
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            for r in conn.execute(
                "SELECT arm, trade_date, wing_width, net_credit, quantity, unmarked_iterations FROM ic_trades"
            ):
                out.append(dict(r))
        finally:
            conn.close()
    return out


def risk_by_session(rows: list[dict]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        held = (float(r["wing_width"] or 0) - float(r["net_credit"] or 0)) * 100 * int(r["quantity"] or 1)
        by_day = out.setdefault(r["arm"], {})
        by_day[r["trade_date"]] = by_day.get(r["trade_date"], 0.0) + held
    return out


def coverage(rows: list[dict], per_arm: dict[str, dict[str, dict]], arms: list[str]) -> dict:
    """Per arm: sessions entered, sessions `w10` entered and the arm did not (the store's wing window
    or the arm's own refusal; the ledger cannot tell them apart), and entries with an unquoted tick."""
    entered = {a: {d for d, s in per_arm.get(a, {}).items() if s["trades"]} for a in arms}
    unmarked: dict[str, int] = {}
    for r in rows:
        if int(r["unmarked_iterations"] or 0) > 0:
            unmarked[r["arm"]] = unmarked.get(r["arm"], 0) + 1
    return {
        a: {
            "sessions_entered": len(entered[a]),
            "w10_entered_arm_did_not": len(entered.get(BASE, set()) - entered[a]),
            "entries_with_unquoted_ticks": unmarked.get(a, 0),
        }
        for a in arms
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--window", required=True, choices=("design", "holdout"))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    out_dir = paths.results_dir() / "research" / "item43"
    out_dir.mkdir(parents=True, exist_ok=True)
    registry = json.loads((home.config_dir() / "meic.risk.json").read_text(encoding="utf-8"))
    reg_path = out_dir / "meic.risk.json"
    reg_path.write_text(json.dumps(study_registry(registry), indent=1), encoding="utf-8")
    os.environ["MEIC_RISK_CONFIG"] = str(reg_path)

    s = _settings.load(None)
    holdout = args.window == "holdout"
    arms = [LIVE, BASE, *GRID, *REPORTED]
    report = runner.run(
        s,
        arms,
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
    rows = ledger_rows(paths.results_dir() / "runs" / report["run_id"])
    risk = risk_by_session(rows)
    base = per_arm.get(BASE, {})
    entered_base = {d for d, x in base.items() if x["trades"]}

    def tests_for(names):
        out = {}
        for a in names:
            mine = per_arm.get(a, {})
            both = sorted(entered_base & {d for d, x in mine.items() if x["trades"]})
            out[a] = {
                "own": own(mine, start, end),
                "vs_w10": paired(mine, base, start, end),
                "vs_w10_both_entered": paired(mine, base, start, end, days=both),
                "per_risk": per_risk(mine, base, risk, a),
            }
        return out

    tests = tests_for(GRID)
    judge(tests)
    doc = {
        "item": 43,
        "window": args.window,
        "span": [start, end],
        "run_id": report["run_id"],
        "provisional": True,
        "refused_sessions": len(report.get("refused") or {}),
        "vix_fallback_sessions": report.get("vix_fallback_sessions"),
        "coverage": coverage(rows, per_arm, arms),
        "reference": {
            BASE: own(base, start, end),
            LIVE: own(per_arm.get(LIVE, {}), start, end),
            f"{BASE}_vs_{LIVE}": paired(base, per_arm.get(LIVE, {}), start, end),
            **tests_for(REPORTED),
        },
        "arms": tests,
        "verdict": "pass" if any(v.get("verdict") == "pass" for v in tests.values()) else "fail",
    }
    path = Path(args.json) if args.json else out_dir / f"study-{args.window}-{start}-{end}.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"],
                      **{a: v.get("verdict") for a, v in tests.items()}}, indent=1))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
