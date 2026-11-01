"""Item 24, the entry slot of MEIC's live rule (docs/edge-hypotheses-plan.md; hypothesis 2's held-out
test): one condor a session entered in a 30-minute slot, 12:00 to 13:30, each paired with the same rule
at 14:00, the slot live trades.

    python scripts/meic_entry_slot_item24.py --window design|holdout --delta D [--workers 8] [--json PATH]
    python scripts/meic_entry_slot_item24.py --annotate FILE [FILE ...]

Runs the backtest's own MEIC replay (`cherrypick.backtest.runner`, MEIC's own engine over the 60 s
vendor store), provisionally because MEIC history has not unlocked. Writes under the backtest data dir
(results/research/item24/), never into the repo: the inputs are licensed.

**The arms, as declared.** `s1400` is the registry's `pm-200` with entries 14:00-14:30 (the 30-minute
spacing makes that one condor a session), no credit floor (the low-IV relief floor too) and no
call-delta ceiling: item 23's `d16`. `s1200`, `s1230`, `s1300` and `s1330` are `s1400` with the entry
window moved to that slot, 30 minutes long. `s1000` and `s1100` are the same at 10:00 and 11:00, for
hypothesis 2's afternoon-against-morning read: references, never judged. `--delta` sets every arm's
short delta: 0.16, or item 23's passing delta, as the plan declares.

**The verdict**, per judged slot, the observation one session (a session with no entry counts, at
zero): the paired difference against `s1400` with its mean above zero and a one-sided t above 2, Holm
across the four slots, positive in both time-halves; and the slot's own net mean above zero, still
positive at 1.5x fees. At least 100 sessions. Each arm's net by year and its worst session are
reported beside it.
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
BASE = "s1400"
SLOTS = {"s1200": "12:00", "s1230": "12:30", "s1300": "13:00", "s1330": "13:30"}
MORNING = {"s1000": "10:00", "s1100": "11:00"}
SLOT_MINUTES = 30
# Item 23's grid: the only deltas this test may run at (16, or item 23's passing arm).
DELTAS = (0.12, 0.16, 0.20, 0.25, 0.30)
# As item 23: no gate refuses one slot more than another (the credit floor binds harder as credit
# decays through the afternoon, and the call ceiling differs by session period).
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


def slot_end(start: str) -> str:
    h, m = map(int, start.split(":"))
    m += SLOT_MINUTES
    return f"{h + m // 60:02d}:{m % 60:02d}"


def study_registry(registry: dict, delta: float) -> dict:
    """The machine's MEIC arm registry with `s1400`, the four judged slots and the two morning ones."""
    if delta not in DELTAS:
        raise SystemExit(f"--delta must be one of {DELTAS}: 0.16, or item 23's passing arm")
    profiles = dict(registry.get("profiles") or {})
    if not isinstance(profiles.get(BASE_ARM), dict):
        raise SystemExit(f"the MEIC registry has no {BASE_ARM!r} arm to build on")
    base = {k: v for k, v in profiles[BASE_ARM].items() if not k.startswith("_")}
    base.update(enabled=True, short_delta_target=delta, **UNGATED)
    for name, start in {BASE: "14:00", **SLOTS, **MORNING}.items():
        profiles[name] = {**base, "paper_entry_window_start": start, "entry_window_end": slot_end(start)}
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


def _mean(xs: list[float]) -> float | None:
    return round(statistics.fmean(xs), 2) if xs else None


def own(sessions: dict[str, dict], start: str, end: str) -> dict:
    """One arm's own sessions ({day: {net, fees, trades}}): its net, halves, years, worst session and
    the fee stress."""
    days = sorted(sessions)
    nets = [sessions[d]["net"] for d in days]
    fees = [sessions[d]["fees"] for d in days]
    mid = midpoint(start, end)
    t = t_stat(nets)
    years: dict[str, list[float]] = {}
    for d in days:
        years.setdefault(d[:4], []).append(sessions[d]["net"])
    worst = min(days, key=lambda d: sessions[d]["net"]) if days else None
    return {
        "n": len(nets),
        "trades": sum(sessions[d]["trades"] for d in days),
        "mean_net": _mean(nets),
        "t": None if t is None else round(t, 3),
        "halves": {
            "first": _mean([sessions[d]["net"] for d in days if d < mid]),
            "second": _mean([sessions[d]["net"] for d in days if d >= mid]),
        },
        "years": {y: _mean(v) for y, v in sorted(years.items())},
        "worst": None if worst is None else {"session": worst, "net": sessions[worst]["net"]},
        "cost_stress": robustness.cost_stress(nets, fees, pass_stress=PASS_STRESS),
        "win_rate": round(sum(x > 0 for x in nets) / len(nets), 3) if nets else None,
    }


def paired(arm: dict[str, dict], base: dict[str, dict], start: str, end: str) -> dict:
    """The arm's session net less `s1400`'s, over the sessions both ran."""
    diffs = {d: arm[d]["net"] - base[d]["net"] for d in sorted(set(arm) & set(base))}
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


def judge(tests: dict[str, dict]) -> None:
    holm = core_stats.holm({k: v["vs_s1400"]["p"] for k, v in tests.items()}, alpha=ALPHA)
    for k, v in tests.items():
        d, o = v["vs_s1400"], v["own"]
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
    """The settings side by side for the studies board, by minute of the day: `s1400` is the live
    slot, the morning slots reported."""
    slots = {BASE: "14:00", **MORNING, **SLOTS}

    def minute(hhmm: str) -> int:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)

    return grid.block(
        doc,
        "entry slot",
        {a: minute(t) for a, t in slots.items()},
        BASE,
        labels=slots,
        sessions=grid.run_sessions(doc.get("run_id")),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--window", choices=("design", "holdout"))
    ap.add_argument("--delta", type=float)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--json", default=None)
    ap.add_argument(
        "--annotate", nargs="+", metavar="FILE", help="add the grid block to result files; no replay"
    )
    args = ap.parse_args(argv)
    if args.annotate:
        print(json.dumps({"annotated": grid.annotate(args.annotate, grid_block)}, indent=1))
        return 0
    if not args.window or args.delta is None:
        ap.error("--window and --delta are required unless --annotate")

    out_dir = paths.results_dir() / "research" / "item24"
    out_dir.mkdir(parents=True, exist_ok=True)
    registry = json.loads((home.config_dir() / "meic.risk.json").read_text(encoding="utf-8"))
    reg_path = out_dir / "meic.risk.json"
    reg_path.write_text(json.dumps(study_registry(registry, args.delta), indent=1), encoding="utf-8")
    os.environ["MEIC_RISK_CONFIG"] = str(reg_path)

    s = _settings.load(None)
    holdout = args.window == "holdout"
    report = runner.run(
        s,
        [BASE, *SLOTS, *MORNING],
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

    def read(a: str) -> dict:
        return {
            "own": own(per_arm.get(a, {}), start, end),
            "vs_s1400": paired(per_arm.get(a, {}), base, start, end),
        }

    tests = {a: read(a) for a in SLOTS}
    judge(tests)
    doc = {
        "item": 24,
        "window": args.window,
        "span": [start, end],
        "delta": args.delta,
        "run_id": report["run_id"],
        "provisional": True,
        "holdout_seen": holdout,
        "refused_sessions": len(report.get("refused") or {}),
        "vix_fallback_sessions": report.get("vix_fallback_sessions"),
        "reference": {BASE: own(base, start, end), **{a: read(a) for a in MORNING}},
        "arms": tests,
        "verdict": "pass" if any(v.get("verdict") == "pass" for v in tests.values()) else "fail",
    }
    doc["grid"] = grid_block(doc)
    name = f"study-{args.window}-{start}-{end}-d{round(args.delta * 100):02d}.json"
    path = Path(args.json) if args.json else out_dir / name
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"],
                      **{a: v.get("verdict") for a, v in tests.items()}}, indent=1))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
