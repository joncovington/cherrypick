"""Item 73, more entries in MEIC's live window (research/edge-hypotheses-plan.md): the rule live trades,
`pm-200` entering 14:00-14:30, at one, two and three condors a session, ten minutes apart. Is the second
condor worth having, and the third?

    python packages/backtest/scripts/meic_1400_stack_item73.py --window design [--workers N]

Runs the backtest's own MEIC replay (`cherrypick.backtest.runner`, MEIC's own engine over the 60 s
vendor store), provisionally because MEIC history has not unlocked. Design window only: `pm-200`'s runs
have read the holdout dates for this window already (the plan's exception), so evidence past the design
has to come from sessions after 2026-10-12. Writes under the backtest data dir (results/research/item73/),
never into the repo: the inputs are licensed.

**The arms, as declared.** Each is the registry's `pm-200` (16 delta, 10 wide, its credit floor and
call-delta ceilings, a per-side stop at 2x the whole credit) with entries 14:00-14:30 and its stop resting
at the broker as live's is (`stop_resting`, limit a tenth of the trigger above). `n1`, `n2` and `n3` take
one, two and three condors a session (`daily_ic_trade_target`, a hard cap under `stagger_entries`), at
least ten minutes apart. `pm-200` blocks no overlap (`overlap_scope` none), so a later condor may sit on
the same strikes as an earlier one. `n1` is live's rule. Nothing else differs.

**Why the differences are the extra condors.** Nothing in these arms lets one entry change another (no
overlap rule, no buying-power cap, no concurrency cap that binds), and the replay is deterministic, so
`n2`'s first condor is `n1`'s and `n3`'s first two are `n2`'s. `n2 - n1`, session by session, is the
second condor's net; `n3 - n2` is the third's. The share of sessions where that holds is reported.

**The verdict**, per increment (`second` = n2 - n1, `third` = n3 - n2), one session one observation (a
session with no extra condor counts at zero), in the design window, all must hold:
- the mean above zero with a one-sided t above 2 (no Holm step: on 100 sessions or more, t above 2 is a
  one-sided p below 0.024, under Holm's strictest bar for two tests, so it could never bind);
- positive in each time-half;
- still positive at 1.5x the increment's own fees;
- the larger arm's mean session net over its 99th-percentile 100-session drawdown at least the smaller
  arm's: the drawdown is bootstrapped from whole sessions, because condors entered minutes apart win and
  lose together and resampling single trades would hide that.
At least 100 sessions. The proposed count is the largest k whose every increment up to k passes.

**Reported, never judged:** each arm's own net, t, halves, worst session and drawdown; each session's
buying power (`(width - credit) x 100` summed over its condors, median and largest); the correlation
of each increment with `n1`'s session net; per arm the share of entries with a side stopped.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

from cherrypick.core import home, robustness
from cherrypick.core.procs import default_workers

from cherrypick.backtest import paths, runner
from cherrypick.backtest import settings as _settings

sys.path.insert(0, str(Path(__file__).parent))
from meic_xsp_item72 import _mean, ledger_rows, midpoint, own, p_one, session_rows, t_stat  # noqa: E402

ITEM = 73
BASE_ARM = "pm-200"
WINDOW = ("14:00", "14:30")
SPACING_MINUTES = 10
COUNTS = {"n1": 1, "n2": 2, "n3": 3}
INCREMENTS = {"second": ("n2", "n1"), "third": ("n3", "n2")}
#: Live's stop rests at the broker (`live.resting_stop`, limit a tenth above the trigger).
RESTING = {"stop_resting": True, "stop_resting_limit_frac": 0.1}
CADENCE = 60
MIN_SESSIONS = 100
T_BAR = 2.0
PASS_STRESS = 1.5
HORIZON = 100


def out_dir() -> Path:
    d = paths.results_dir() / "research" / f"item{ITEM}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def study_registry(registry: dict) -> dict:
    """The machine's MEIC arm registry with `n1`, `n2` and `n3` added."""
    profiles = dict(registry.get("profiles") or {})
    if not isinstance(profiles.get(BASE_ARM), dict):
        raise SystemExit(f"the MEIC registry has no {BASE_ARM!r} arm to build on")
    live = {k: v for k, v in profiles[BASE_ARM].items() if not k.startswith("_")}
    if float(live.get("stop_trigger_ratio", 0)) != 2.0 or not live.get("per_side_stop_management"):
        raise SystemExit(f"{BASE_ARM!r} no longer stops at 2.0x: the arms are declared on it")
    if (live.get("overlap_scope") or "none") != "none":
        raise SystemExit(f"{BASE_ARM!r} now blocks overlap: the increments would no longer be separable")
    live.update(
        enabled=True,
        paper_entry_window_start=WINDOW[0],
        entry_window_end=WINDOW[1],
        stagger_entries=True,
        min_minutes_between_entries=SPACING_MINUTES,
        **RESTING,
    )
    for name, n in COUNTS.items():
        profiles[name] = {**live, "daily_ic_trade_target": n}
    return {**registry, "profiles": profiles}


def by_arm_day(rows: list[dict]) -> dict[str, dict[str, list[dict]]]:
    out: dict[str, dict[str, list[dict]]] = {}
    for r in rows:
        out.setdefault(r.get("arm"), {}).setdefault(r.get("trade_date"), []).append(r)
    for days in out.values():
        for v in days.values():
            v.sort(key=lambda r: r.get("entry_time") or "")
    return out


def _key(r: dict) -> tuple:
    return (r.get("entry_time"), r.get("put_strike"), r.get("call_strike"), r.get("net_credit"))


def nested_share(small: dict[str, list[dict]], large: dict[str, list[dict]]) -> float | None:
    """The share of sessions where the larger arm's first entries are the smaller arm's, entry for entry."""
    days = sorted(set(small) | set(large))
    if not days:
        return None
    same = sum(
        1
        for d in days
        if [_key(r) for r in small.get(d, [])] == [_key(r) for r in large.get(d, [])][: len(small.get(d, []))]
    )
    return round(same / len(days), 4)


def session_bp(day_rows: list[dict]) -> float:
    """The session's condors' buying power summed, `(width - credit) x 100 x quantity` each: what an
    account must hold free for them all, since they open within half an hour and settle together."""
    return round(
        sum(
            (float(r.get("wing_width") or 0) - float(r.get("net_credit") or 0))
            * 100
            * int(r.get("quantity") or 1)
            for r in day_rows
        ),
        2,
    )


def session_dd(sessions: dict[str, dict]) -> dict:
    """The 99th-percentile drawdown over `HORIZON` sessions, bootstrapped from whole sessions."""
    nets = [sessions[d]["net"] for d in sorted(sessions)]
    dr = robustness.drawdown_range(nets, sessions=len(nets) or None, horizon=HORIZON)
    p99 = None if dr.get("insufficient") else dr.get("drawdown_block")
    mean = statistics.fmean(nets) if nets else None
    return {
        "drawdown_p99": p99,
        "mean_over_dd": None if not p99 or mean is None else round(mean / p99, 6),
        "insufficient": bool(dr.get("insufficient")),
    }


def increment(large: dict[str, dict], small: dict[str, dict], start: str, end: str) -> dict:
    days = sorted(set(large) | set(small))
    diffs = [(large.get(d) or {}).get("net", 0.0) - (small.get(d) or {}).get("net", 0.0) for d in days]
    fees = [(large.get(d) or {}).get("fees", 0.0) - (small.get(d) or {}).get("fees", 0.0) for d in days]
    base = [(small.get(d) or {}).get("net", 0.0) for d in days]
    mid = midpoint(start, end)
    t = t_stat(diffs)
    corr = None
    if len(diffs) > 2 and statistics.pstdev(diffs) > 0 and statistics.pstdev(base) > 0:
        corr = round(statistics.correlation(diffs, base), 4)
    return {
        "n": len(diffs),
        "sessions_with_extra": sum(
            1 for d in days if (large.get(d) or {}).get("trades", 0) > (small.get(d) or {}).get("trades", 0)
        ),
        "mean": _mean(diffs),
        "t": None if t is None else round(t, 3),
        "p": p_one(t, len(diffs)),
        "halves": {
            "first": _mean([x for d, x in zip(days, diffs, strict=True) if d < mid]),
            "second": _mean([x for d, x in zip(days, diffs, strict=True) if d >= mid]),
        },
        "cost_stress": robustness.cost_stress(diffs, fees, pass_stress=PASS_STRESS),
        "worst_session": min(diffs) if diffs else None,
        "corr_with_smaller_arm": corr,
    }


def judge(incs: dict[str, dict], arms: dict[str, dict]) -> None:
    for k, v in incs.items():
        large, small = INCREMENTS[k]
        if v["n"] < MIN_SESSIONS:
            v["verdict"], v["why"] = "no verdict", f"{v['n']} sessions < {MIN_SESSIONS}"
            continue
        a, b = arms[large]["drawdown"]["mean_over_dd"], arms[small]["drawdown"]["mean_over_dd"]
        v["checks"] = {
            "mean_positive": (v["mean"] or 0) > 0,
            "t_above_bar": (v["t"] or 0) > T_BAR,
            "both_halves_positive": all((x or 0) > 0 for x in v["halves"].values()),
            "survives_1_5x_fees": v["cost_stress"]["survives"],
            "mean_over_dd_at_least_smaller": a is not None and b is not None and a >= b,
        }
        v["verdict"] = "pass" if all(v["checks"].values()) else "fail"


def proposed_count(incs: dict[str, dict]) -> int:
    k = 1
    for name in ("second", "third"):
        if incs[name].get("verdict") != "pass":
            break
        k += 1
    return k


def stopped_share(rows: list[dict]) -> float | None:
    if not rows:
        return None
    hit = sum(1 for r in rows if r.get("put_stop_cost") is not None or r.get("call_stop_cost") is not None)
    return round(hit / len(rows), 4)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--window", choices=("design",), required=True)
    ap.add_argument("--workers", type=int, default=default_workers())
    args = ap.parse_args(argv)

    registry = json.loads((home.config_dir() / "meic.risk.json").read_text(encoding="utf-8"))
    reg_path = out_dir() / "meic.risk.json"
    reg_path.write_text(json.dumps(study_registry(registry), indent=1), encoding="utf-8")
    os.environ["MEIC_RISK_CONFIG"] = str(reg_path)

    s = _settings.load(None)
    report = runner.run(
        s,
        list(COUNTS),
        args.window,
        module="meic",
        cadence=CADENCE,
        provisional=True,
        workers=args.workers,
        log=lambda m: print(m, file=sys.stderr),
    )
    start, end = s["windows"][args.window]
    sess = session_rows(report["run_id"])
    rows = ledger_rows(paths.results_dir() / "runs" / report["run_id"])
    ledger = by_arm_day([r for r in rows if r.get("arm") in COUNTS])

    arms = {}
    for name in COUNTS:
        mine = sess.get(name, {})
        days = ledger.get(name, {})
        bps = [session_bp(v) for v in days.values()]
        arms[name] = {
            "own": own(mine, start, end),
            "drawdown": session_dd(mine),
            "session_bp_median": statistics.median(bps) if bps else None,
            "session_bp_max": max(bps) if bps else None,
            "stopped_share": stopped_share([r for v in days.values() for r in v]),
        }
    incs = {}
    for k, (large, small) in INCREMENTS.items():
        incs[k] = {
            **increment(sess.get(large, {}), sess.get(small, {}), start, end),
            "nested_share": nested_share(ledger.get(small, {}), ledger.get(large, {})),
        }
    judge(incs, arms)
    doc = {
        "item": ITEM,
        "window": args.window,
        "span": [start, end],
        "run_id": report["run_id"],
        "provisional": True,
        "refused_sessions": len(report.get("refused") or {}),
        "vix_fallback_sessions": report.get("vix_fallback_sessions"),
        "arms": arms,
        "increments": incs,
        "proposed_count": proposed_count(incs),
    }
    path = out_dir() / f"study-{args.window}-{start}-{end}.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "proposed_count": doc["proposed_count"],
                      **{k: v.get("verdict") for k, v in incs.items()}}, indent=1))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
