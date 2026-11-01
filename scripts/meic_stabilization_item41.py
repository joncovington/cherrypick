"""Item 41, the stabilization entry gate at real quotes (docs/edge-hypotheses-plan.md): the break-even
condor at 10-point wings with and without the gate, and the 14:00 rule with and without it.

    python scripts/meic_stabilization_item41.py --step plan
    python scripts/meic_stabilization_item41.py --step study --window design|holdout [--workers 8]

Runs the backtest's own MEIC replay (`cherrypick.backtest.runner`, MEIC's own engine over the 60 s
vendor store), provisionally because MEIC history has not unlocked. Writes under the backtest data dir
(results/research/item41/), never into the repo: the inputs are licensed.

**The plan step** reads spot bars only, no P&L: over the design window, the share of 09:45-15:30 ticks
on which the `multi` oracle (2 bars) does not read STABLE. The verdict changes only at a bar close, so
it is read once per five-minute interval, each interval standing for its five 60 s ticks. That share
is `be10-skip`'s placebo rate; the study step refuses to run without it.

**The arms, as declared.** `be10` is the `be-hourly-1x` profile (docs/theta-profits-be-condor.md) with
both wings 10 points and `equalize_credit` off. Judged: `be10-multi`, `be10-perday` and `be10-multi-3b`
against `be10`; `live-1400-multi` (item 23's `live-1400` with the gate) against `live-1400`. Reported:
`be10-skip`, `live-1400`, `control`.

**The verdict**, per judged arm, one session one observation (a session with no entry counts at zero):
the paired difference against its base with a mean above zero and a one-sided t above 2, Holm across
the four, positive in both time-halves; the arm's own net above zero and still positive at 1.5x fees;
then the holdout's paired mean above zero with t above 2. At least 100 sessions.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.backtest import paths, results, runner, store
from cherrypick.backtest import settings as _settings
from cherrypick.core import home, robustness
from cherrypick.core import stats as core_stats

ITEM = 41
SYMBOL = "SPX"
CADENCE = 60
MIN_SESSIONS = 100
T_BAR = 2.0
ALPHA = 0.05
PASS_STRESS = 1.5
PLACEBO_SEED = 41
ET = ZoneInfo("America/New_York")
WINDOW = ("09:45", "15:30")

# docs/theta-profits-be-condor.md, "The proposed arm", with both wings 10 points: the store holds
# about 100 points of strikes either side of spot, too few for his 25-40 (item 42).
BE10 = {
    "enabled": True,
    "symbols": ["SPX"],
    "min_iv_rank": 0.0,
    "min_credit_pct_of_width": 0.05,
    "max_call_delta_entry": 0.2,
    "max_call_delta_entry_open_volatile": 0.19,
    "max_call_delta_entry_late": 0.19,
    "min_call_otm_pct": 0.0001,
    "min_put_otm_pct": 0.0001,
    "late_entry_bias_enabled": False,
    "regime_vix_pause_threshold": 999,
    "regime_atr_pause_threshold_pct": None,
    "regime_vix1d_ratio_pause_threshold": 999,
    "regime_gex_block_negative": False,
    "regime_gex_require_positive": False,
    "regime_gex_min_flip_distance_pct": None,
    "delta_target": 0.12,
    "delta_target_vix_low": 0.12,
    "delta_target_vix_elevated": 0.12,
    "delta_target_vix_high": 0.12,
    "delta_target_vix_crisis": 0.12,
    "wing_widths_by_symbol": {"SPX": [10]},
    "equalize_credit": False,
    "separate_spread_entry": True,
    "paper_entry_window_start": WINDOW[0],
    "entry_window_end": WINDOW[1],
    "per_side_stop_management": True,
    "stop_trigger_ratio": 1.0,
    "overlap_scope": "none",
    "stagger_entries": True,
    "min_minutes_between_entries": 55,
    "max_concurrent_ics": 10,
    "daily_ic_trade_target": 7,
}
GATE = {"stabilization_entry": True, "stabilization_threshold_mode": "atr"}
LIVE_BASE_ARM = "pm-200"
LIVE = "live-1400"
# base -> judged arms paired against it
JUDGED = {
    "be10": ["be10-multi", "be10-perday", "be10-multi-3b"],
    LIVE: ["live-1400-multi"],
}
PLACEBO = "be10-skip"


def out_dir() -> Path:
    d = paths.results_dir() / "research" / f"item{ITEM}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def require_gate():
    """The gate's keys and the replay's wiring (PR #728), or refuse: without them every gated arm
    would wait all day and the study would read as the gate losing."""
    try:
        from cherrypick.meic import replay, stabilization_gate
    except ImportError as exc:
        raise SystemExit(f"item {ITEM} needs PR #728 merged ({exc})") from exc

    missing = [
        n for n in ("verdict_key", "placebo_skips", "all_verdicts") if not hasattr(stabilization_gate, n)
    ]
    if missing or "stabilization_gate" not in vars(replay):
        raise SystemExit(f"item {ITEM} needs PR #728 merged (missing: {missing or ['replay wiring']})")
    return stabilization_gate


def study_registry(registry: dict, placebo_rate: float) -> dict:
    """The machine's MEIC arm registry with item 41's arms added."""
    profiles = dict(registry.get("profiles") or {})
    if not isinstance(profiles.get(LIVE_BASE_ARM), dict):
        raise SystemExit(f"the MEIC registry has no {LIVE_BASE_ARM!r} arm to build on")
    live = {k: v for k, v in profiles[LIVE_BASE_ARM].items() if not k.startswith("_")}
    live.update(enabled=True, paper_entry_window_start="14:00", entry_window_end="14:30")
    profiles[LIVE] = live
    profiles["live-1400-multi"] = {**live, **GATE, "stabilization_oracle": "multi", "stabilization_bars": 2}
    profiles["be10"] = dict(BE10)
    profiles["be10-multi"] = {**BE10, **GATE, "stabilization_oracle": "multi", "stabilization_bars": 2}
    profiles["be10-perday"] = {**BE10, **GATE, "stabilization_oracle": "perday", "stabilization_bars": 2}
    profiles["be10-multi-3b"] = {**BE10, **GATE, "stabilization_oracle": "multi", "stabilization_bars": 3}
    profiles[PLACEBO] = {
        **BE10,
        "stabilization_placebo_rate": placebo_rate,
        "stabilization_placebo_seed": PLACEBO_SEED,
    }
    return {**registry, "profiles": profiles}


# ── plan step ────────────────────────────────────────────────────────────────


def refusals(settings: dict, window: tuple[str, str] = WINDOW) -> tuple[list[str], int, int]:
    """Over the design window's sessions in `settings`' store: the sessions, and the five-minute
    intervals inside `window` (ET) on which `multi` (2 bars, ATR) is and is not STABLE."""
    gate = require_gate()
    start, end = settings["windows"]["design"]
    db = store.db_path(settings, SYMBOL, CADENCE)
    vconn = store.connect(settings, SYMBOL, CADENCE)
    try:
        days = [d for d, _ in runner.sessions_in(vconn, SYMBOL, start, end)]
    finally:
        vconn.close()
    lo = datetime.strptime(window[0], "%H:%M")
    hi = datetime.strptime(window[1], "%H:%M")
    stable = refused = 0
    for day in days:
        t = lo
        while t < hi:
            # The verdict holding over the interval [t, t+5): read at t, after the bar closing at t.
            now = datetime.combine(date.fromisoformat(day), t.time(), tzinfo=ET)
            v = gate.snapshot_verdicts(SYMBOL, day, now, trail_path=db)["multi"]
            if v is True:
                stable += 1
            else:
                refused += 1
            t += timedelta(minutes=5)
    return days, stable, refused


def plan(settings: dict) -> dict:
    start, end = settings["windows"]["design"]
    days, stable, refused = refusals(settings)
    total = stable + refused
    doc = {
        "item": ITEM,
        "step": "plan",
        "span": [start, end],
        "sessions": len(days),
        "intervals": total,
        "refusal_rate": round(refused / total, 4) if total else None,
        "note": "share of 09:45-15:30 five-minute intervals on which multi (2 bars, ATR) is not STABLE; "
        "spot only, no P&L",
    }
    (out_dir() / "plan.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


# ── study step ───────────────────────────────────────────────────────────────


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
    days = sorted(sessions)
    nets = [sessions[d]["net"] for d in days]
    fees = [sessions[d]["fees"] for d in days]
    trades = sum(sessions[d]["trades"] or 0 for d in days)
    mid = midpoint(start, end)
    t = t_stat(nets)
    years: dict[str, list[float]] = {}
    for d in days:
        years.setdefault(d[:4], []).append(sessions[d]["net"])
    return {
        "n": len(nets),
        "trades": trades,
        "trades_per_session": round(trades / len(nets), 3) if nets else None,
        "net_per_trade": round(sum(nets) / trades, 2) if trades else None,
        "mean_net": _mean(nets),
        "t": None if t is None else round(t, 3),
        "halves": {
            "first": _mean([sessions[d]["net"] for d in days if d < mid]),
            "second": _mean([sessions[d]["net"] for d in days if d >= mid]),
        },
        "by_year": {y: _mean(v) for y, v in sorted(years.items())},
        "worst_session": round(min(nets), 2) if nets else None,
        "cost_stress": robustness.cost_stress(nets, fees, pass_stress=PASS_STRESS),
    }


def paired(arm: dict[str, dict], base: dict[str, dict], start: str, end: str) -> dict:
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


def judge(tests: dict[str, dict], holdout: bool) -> None:
    holm = core_stats.holm({k: v["vs_base"]["p"] for k, v in tests.items()}, alpha=ALPHA)
    for k, v in tests.items():
        d, o = v["vs_base"], v["own"]
        v["holm_ok"] = holm[k]
        if d["n"] < MIN_SESSIONS:
            v["verdict"] = "no verdict"
            v["why"] = f"{d['n']} sessions < {MIN_SESSIONS}"
            continue
        if holdout:
            checks = {
                "paired_mean_positive": (d["mean"] or 0) > 0,
                "paired_t_above_bar": (d["t"] or 0) > T_BAR,
            }
        else:
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


def item23_live_holdout() -> tuple[dict[str, dict], str | None]:
    """Item 23's recorded `live-1400` holdout sessions (the runner will not spend that spec twice)."""
    files = sorted((paths.results_dir() / "research" / "item23").glob("study-holdout-*.json"))
    if not files:
        return {}, None
    run_id = json.loads(files[-1].read_text(encoding="utf-8")).get("run_id")
    return (session_rows(run_id).get(LIVE, {}) if run_id else {}), run_id


def study(settings: dict, window: str, workers: int) -> dict:
    require_gate()
    plan_path = out_dir() / "plan.json"
    if not plan_path.exists():
        raise SystemExit("run --step plan first: the placebo's rate comes from it")
    rate = json.loads(plan_path.read_text(encoding="utf-8"))["refusal_rate"]
    registry = json.loads((home.config_dir() / "meic.risk.json").read_text(encoding="utf-8"))
    reg_path = out_dir() / "meic.risk.json"
    reg_path.write_text(json.dumps(study_registry(registry, rate), indent=1), encoding="utf-8")
    os.environ["MEIC_RISK_CONFIG"] = str(reg_path)

    holdout = window == "holdout"
    arms = ["be10", *JUDGED["be10"], PLACEBO, *JUDGED[LIVE]]
    if not holdout:
        arms.insert(0, LIVE)
    report = runner.run(
        settings,
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
    per_arm = session_rows(report["run_id"])
    live_base, live_run = item23_live_holdout() if holdout else (per_arm.get(LIVE, {}), report["run_id"])
    bases = {"be10": per_arm.get("be10", {}), LIVE: live_base}
    tests = {
        a: {
            "base": b,
            "own": own(per_arm.get(a, {}), start, end),
            "vs_base": paired(per_arm.get(a, {}), bases[b], start, end),
        }
        for b, judged in JUDGED.items()
        for a in judged
    }
    judge(tests, holdout)
    doc = {
        "item": ITEM,
        "window": window,
        "span": [start, end],
        "run_id": report["run_id"],
        "live_1400_run_id": live_run,
        "provisional": True,
        "placebo_rate": rate,
        "refused_sessions": len(report.get("refused") or {}),
        "reference": {
            "be10": own(bases["be10"], start, end),
            LIVE: own(live_base, start, end),
            PLACEBO: own(per_arm.get(PLACEBO, {}), start, end),
            f"{PLACEBO}_vs_be10": paired(per_arm.get(PLACEBO, {}), bases["be10"], start, end),
            **{
                f"{a}_vs_{PLACEBO}": paired(per_arm.get(a, {}), per_arm.get(PLACEBO, {}), start, end)
                for a in JUDGED["be10"]
            },
        },
        "arms": tests,
        "verdict": "pass" if any(v.get("verdict") == "pass" for v in tests.values()) else "fail",
    }
    path = out_dir() / f"study-{window}-{start}-{end}.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    return {
        "written": str(path),
        "verdict": doc["verdict"],
        **{a: v.get("verdict") for a, v in tests.items()},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--step", required=True, choices=("plan", "study"))
    ap.add_argument("--window", choices=("design", "holdout"), default="design")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args(argv)
    s = _settings.load(None)
    out = plan(s) if args.step == "plan" else study(s, args.window, args.workers)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
