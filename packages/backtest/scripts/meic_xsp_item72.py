"""Item 72, MEIC's live rule on XSP across stop rules and wing widths (research/edge-hypotheses-plan.md):
`pm-200` entering once a day, 14:00-14:30, on XSP with no stop, or a per-side stop at 1x, 1.5x or 2x the
whole credit, each at 1, 2, 3 and 4 points wide, paired against the rule live trades (2x, 1 point).

    python packages/backtest/scripts/meic_xsp_item72.py --step coverage
    python packages/backtest/scripts/meic_xsp_item72.py --step study --window design|holdout --capital C [--workers N]
    python packages/backtest/scripts/meic_xsp_item72.py --step kill

Runs the backtest's own MEIC replay (`cherrypick.backtest.runner`, MEIC's own engine) over XSP's wide
60 s vendor store (`XSP-60s-wide.db`), provisionally: MEIC history has not unlocked, and XSP cannot
unlock it here (nothing records XSP to validate against). The holdout is spent by this run, once
(`unvalidated_holdout`). Writes under the backtest data dir (results/research/item72/), never into the
repo: the inputs are licensed.

**The arms, as declared.** Each is the registry's `pm-200` with entries 14:00-14:30 (the rule live
trades, gates and all), `symbols` set to XSP and `wing_width_points` set to 1, 2, 3 or 4. Its stop is
one of: none (`per_side_stop_management` off, held to the close), or a per-side stop at 1.0, 1.5 or 2.0
times the whole credit, each resting at the broker as live's stop does (`stop_resting`, its limit a
tenth of the trigger above it). `s20-w1` is the rule live trades and the reference. `control` runs
because the runner requires it, pointed at XSP, and is never judged.

**The verdict.** The observation is one session (a session with no entry counts, at zero).
- The reference: its own mean net above zero with a one-sided t above 2, positive in both time-halves,
  still positive at 1.5x fees; at least 100 sessions; then the holdout's mean above zero with t above 2.
- Each of the other fifteen, paired with the reference session by session: the mean difference above
  zero with a one-sided t above 2, Holm across the fifteen, the difference positive in both halves, the
  arm's own net still positive at 1.5x fees, and, wider than one point, its net over buying power at
  least the reference's over the same sessions; then the holdout's paired mean above zero with t above 2.

**Reported, never judged:** each arm's sizing against `--capital` (its worst session, the
99th-percentile drawdown over 100 sessions from its own trades, both as shares of the capital, and the
most whole condors that drawdown allows within 30% of it); the reference's fill stress in the design
window (halfway from mid to the natural fill); per arm the share of entries with a side stopped.

**The kill step** derives the reference's kill criteria from its own trades over both windows
(`core.killcriteria`, the derivation main's were made with) and writes the block, ready for a person
to put in the live config. It never writes config.

**The coverage step** reads quotes only, no P&L, and must come first: sessions per window, strike
spacing near spot, and the share of hourly 16-delta shorts whose wing is quoted at each width.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import statistics
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import home, killcriteria, robustness
from cherrypick.core import progress as core_progress
from cherrypick.core import stats as core_stats
from cherrypick.core.procs import default_workers

from cherrypick.backtest import paths, results, runner, store
from cherrypick.backtest import settings as _settings

ITEM = 72
SYMBOL = "XSP"
TAG = "wide"
CADENCE = 60
ET = ZoneInfo("America/New_York")
BASE_ARM = "pm-200"
WINDOW = ("14:00", "14:30")
#: The stop rules: None is no stop (held to the close); a number is the per-side trigger as a multiple of
#: the whole credit.
STOPS: dict[str, float | None] = {"none": None, "s10": 1.0, "s15": 1.5, "s20": 2.0}
WIDTHS = (1, 2, 3, 4)
#: A stopped arm's stop rests at the broker, as live's does (`live.resting_stop`, limit a tenth above).
RESTING = {"stop_resting": True, "stop_resting_limit_frac": 0.1}
REFERENCE = "s20-w1"
HALFWAY = {"slippage_frac_of_spread": 0.25}
HALFWAY_SUFFIX = "-halfway"
SHORT_DELTA = 0.16
HOURS = (10, 11, 12, 13, 14, 15)
MIN_SESSIONS = 100
T_BAR = 2.0
ALPHA = 0.05
PASS_STRESS = 1.5
SIZING_SHARE = 0.30
SIZING_HORIZON = 100


def arm_name(stop: str, width: int) -> str:
    return f"{stop}-w{width}"


ARMS: dict[str, tuple[str, int]] = {arm_name(s, w): (s, w) for s in STOPS for w in WIDTHS}
CHALLENGERS = [a for a in ARMS if a != REFERENCE]


def out_dir() -> Path:
    d = paths.results_dir() / "research" / f"item{ITEM}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def wide(settings: dict) -> dict:
    return store.tagged(settings, TAG)


def arm_params(live: dict, stop: str, width: int) -> dict:
    """One arm: live's rule at `width` points with `stop`'s rule."""
    ratio = STOPS[stop]
    p = {**live, "wing_width_points": width}
    for k in RESTING:
        p.pop(k, None)
    if ratio is None:
        p["per_side_stop_management"] = False
    else:
        p.update(per_side_stop_management=True, stop_trigger_ratio=ratio, **RESTING)
    return p


def study_registry(registry: dict, *, stress: bool) -> dict:
    """The machine's MEIC registry with this study's arms added (and the reference's stress-fill twin
    in the design window), `control` pointed at XSP so the runner's paired arm can trade it."""
    profiles = dict(registry.get("profiles") or {})
    if not isinstance(profiles.get(BASE_ARM), dict):
        raise SystemExit(f"the MEIC registry has no {BASE_ARM!r} arm to build on")
    live = {k: v for k, v in profiles[BASE_ARM].items() if not k.startswith("_")}
    live.update(enabled=True, paper_entry_window_start=WINDOW[0], entry_window_end=WINDOW[1], symbols=[SYMBOL])
    for name, (stop, width) in ARMS.items():
        profiles[name] = arm_params(live, stop, width)
    if stress:
        profiles[REFERENCE + HALFWAY_SUFFIX] = {**profiles[REFERENCE], **HALFWAY}
    control = profiles.get("control")
    if isinstance(control, dict):
        profiles["control"] = {**control, "symbols": [SYMBOL]}
    return {**registry, "profiles": profiles}


# ── coverage step ────────────────────────────────────────────────────────────


def wing_inside(quotes: list[tuple[int, float, float | None]], width: float) -> dict[str, bool | None]:
    """For one sample's quotes (side, strike, delta): whether the 16-delta put's and call's `width`
    wings are quoted in it. None for a side with no delta to pick a short by."""
    out: dict[str, bool | None] = {}
    for name, side, sign in (("put", store.SIDES["PUT"], -1), ("call", store.SIDES["CALL"], 1)):
        legs = [(k, d) for s, k, d in quotes if s == side and d is not None]
        if not legs:
            out[name] = None
            continue
        short = min(legs, key=lambda kd: abs(abs(kd[1]) - SHORT_DELTA))[0]
        have = {float(k) for s, k, _ in quotes if s == side}
        out[name] = float(short + sign * width) in have
    return out


def spacing(quotes: list[tuple[int, float, float | None]], spot: float | None) -> float | None:
    """The smallest gap between adjacent stored strikes within 1% of spot."""
    if not spot:
        return None
    ks = sorted({float(k) for _, k, _ in quotes if abs(float(k) - spot) <= 0.01 * spot})
    gaps = [b - a for a, b in zip(ks, ks[1:], strict=False) if b > a]
    return min(gaps) if gaps else None


def _session_marks(conn: sqlite3.Connection, day: str) -> list[dict]:
    samples = conn.execute(
        "SELECT id, ts FROM gex_quote_samples WHERE symbol = ? AND trade_date = ? ORDER BY ts", (SYMBOL, day)
    ).fetchall()
    out = []
    for hh in HOURS:
        target = datetime.fromisoformat(day).replace(hour=hh, tzinfo=ET).timestamp()
        hit = next(((i, ts) for i, ts in samples if ts >= target), None)
        if hit is None:
            continue
        quotes = conn.execute("SELECT side, strike, delta FROM gex_quotes WHERE sample_id = ?", (hit[0],)).fetchall()
        spot = conn.execute(
            "SELECT spot FROM gex_spot_history WHERE symbol = ? AND trade_date = ? AND ts <= ? ORDER BY ts DESC LIMIT 1",
            (SYMBOL, day, hit[1]),
        ).fetchone()
        out.append(
            {"spacing": spacing(quotes, spot[0] if spot else None), **{w: wing_inside(quotes, float(w)) for w in WIDTHS}}
        )
    return out


def _share(xs: list[bool | None]) -> float | None:
    known = [x for x in xs if x is not None]
    return round(sum(known) / len(known), 4) if known else None


def coverage(settings: dict) -> dict:
    s = wide(settings)
    path = store.db_path(s, SYMBOL, CADENCE)
    doc: dict = {"item": ITEM, "step": "coverage", "symbol": SYMBOL, "store": str(path), "windows": {}}
    with core_progress.Reporter(f"item{ITEM}-coverage") as rep:
        rep.phase("windows", total=len(settings["windows"]), unit="windows")
        for window, (start, end) in settings["windows"].items():
            row: dict = {"span": [start, end], "sessions": 0}
            if path.exists():
                conn = store.connect(s, SYMBOL, CADENCE)
                try:
                    days = [d for d, _ in runner.sessions_in(conn, SYMBOL, start, end)]
                    row["sessions"] = len(days)
                    row["first"], row["last"] = (days[0], days[-1]) if days else (None, None)
                    marks = [m for d in days for m in _session_marks(conn, d)]
                    gaps = [m["spacing"] for m in marks if m["spacing"]]
                    row["hourly_marks"] = len(marks)
                    row["strike_spacing_median"] = statistics.median(gaps) if gaps else None
                    for w in WIDTHS:
                        row[f"w{w}_put_wing_inside"] = _share([m[w]["put"] for m in marks])
                        row[f"w{w}_call_wing_inside"] = _share([m[w]["call"] for m in marks])
                finally:
                    conn.close()
            doc["windows"][window] = row
            rep.advance("windows", current=window)
    doc["note"] = (
        f"share of hourly {SHORT_DELTA:g}-delta shorts ({HOURS[0]}:00-{HOURS[-1]}:00) whose wing is quoted in "
        "the same sample, per width; strike spacing within 1% of spot; quotes only, no P&L"
    )
    (out_dir() / "coverage.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


def coverage_refusal(doc: dict | None, window: str) -> str | None:
    """Why the study may not run yet: no coverage read, or no session in the window."""
    if not doc:
        return "run --step coverage first: the store's reach is reported before any P&L"
    if not ((doc.get("windows") or {}).get(window) or {}).get("sessions"):
        return f"no {window} session in the coverage read for {SYMBOL}: fetch, then re-run coverage"
    return None


# ── study step ───────────────────────────────────────────────────────────────


def t_stat(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    sd = statistics.stdev(xs)
    return statistics.fmean(xs) / (sd / math.sqrt(len(xs))) if sd > 0 else None


def p_one(t: float | None, n: int) -> float:
    return 1.0 if t is None or n < 2 else 1.0 - core_stats.t_cdf(t, n - 1)


def _mean(xs: list[float]) -> float | None:
    return round(statistics.fmean(xs), 2) if xs else None


def midpoint(start: str, end: str) -> str:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return date.fromordinal((a.toordinal() + b.toordinal()) // 2).isoformat()


def own(sessions: dict[str, dict], start: str, end: str) -> dict:
    """One arm's sessions ({day: {net, fees, trades}}): its net, t, halves and the fee stress."""
    days = sorted(sessions)
    nets = [sessions[d]["net"] for d in days]
    fees = [sessions[d]["fees"] for d in days]
    mid = midpoint(start, end)
    t = t_stat(nets)
    return {
        "n": len(nets),
        "trades": sum(sessions[d]["trades"] for d in days),
        "mean_net": _mean(nets),
        "t": None if t is None else round(t, 3),
        "p": p_one(t, len(nets)),
        "halves": {
            "first": _mean([sessions[d]["net"] for d in days if d < mid]),
            "second": _mean([sessions[d]["net"] for d in days if d >= mid]),
        },
        "cost_stress": robustness.cost_stress(nets, fees, pass_stress=PASS_STRESS),
        "worst_session": min(nets) if nets else None,
    }


def paired(arm: dict[str, dict], ref: dict[str, dict], start: str, end: str) -> dict:
    """The arm's session net minus the reference's, session by session over every session either ran
    (a session one of them has no row for counts at zero for it)."""
    days = sorted(set(arm) | set(ref))
    diffs = [(arm.get(d) or {}).get("net", 0.0) - (ref.get(d) or {}).get("net", 0.0) for d in days]
    mid = midpoint(start, end)
    t = t_stat(diffs)
    return {
        "n": len(diffs),
        "mean_diff": _mean(diffs),
        "t": None if t is None else round(t, 3),
        "p": p_one(t, len(diffs)),
        "halves": {
            "first": _mean([x for d, x in zip(days, diffs, strict=True) if d < mid]),
            "second": _mean([x for d, x in zip(days, diffs, strict=True) if d >= mid]),
        },
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


def ledger_rows(ledger_dir: Path) -> list[dict]:
    """Every entry the run's per-session ledgers hold: arm, session, width, credit, quantity, sides."""
    out = []
    for db in sorted(ledger_dir.glob("*.db")):
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            for r in conn.execute("SELECT * FROM ic_trades"):
                out.append(dict(r))
        except sqlite3.OperationalError:
            continue  # a ledger the replay never initialised holds no entry
        finally:
            conn.close()
    return out


def risk_by_session(rows: list[dict]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        held = (float(r.get("wing_width") or 0) - float(r.get("net_credit") or 0)) * 100 * int(r.get("quantity") or 1)
        by_day = out.setdefault(r["arm"], {})
        by_day[r["trade_date"]] = by_day.get(r["trade_date"], 0.0) + held
    return out


def per_risk(sessions: dict[str, dict], risk: dict[str, float], days: list[str]) -> float | None:
    """Summed net over the summed `(width - credit) x 100 x quantity` of every entry, over `days`."""
    held = sum(risk.get(d, 0.0) for d in days)
    return round(sum(sessions[d]["net"] for d in days if d in sessions) / held, 5) if held > 0 else None


def stopped_share(rows: list[dict], arm: str) -> float | None:
    """The share of the arm's entries with a side closed by its stop."""
    mine = [r for r in rows if r.get("arm") == arm]
    if not mine:
        return None
    # A side's stop cost is set when it is stopped (paper's own `was_stopped` reads the same columns).
    hit = sum(1 for r in mine if r.get("put_stop_cost") is not None or r.get("call_stop_cost") is not None)
    return round(hit / len(mine), 4)


def trade_nets(run_dir: Path, arm: str) -> list[dict]:
    """The arm's trades in the run's ledgers, read the way the kill criteria are derived."""
    rows = [
        r
        for r in killcriteria.evidence_records(run_dir, "meic_ic")
        if r.get("arm") == arm and r.get("net_pnl") is not None
    ]
    return sorted(rows, key=lambda r: r.get("session") or "")


def sizing(trades: list[dict], sessions: dict[str, dict], capital: float) -> dict:
    """What `capital` carries of the arm: its worst session and its 99th-percentile drawdown over
    `SIZING_HORIZON` sessions (the bootstrap kill criteria use), as shares, and the most whole condors
    within `SIZING_SHARE` of it. Description only."""
    nets = [float(r["net_pnl"]) for r in trades]
    worst = min((v["net"] for v in sessions.values()), default=None)
    dr = robustness.drawdown_range(nets, sessions=len(sessions) or None, horizon=SIZING_HORIZON)
    p99 = None if dr.get("insufficient") else dr.get("drawdown_block")
    return {
        "capital": capital,
        "worst_session": worst,
        "worst_session_share": round(-worst / capital, 4) if worst is not None and worst < 0 else 0.0,
        "drawdown_p99": p99,
        "drawdown_p99_share": round(p99 / capital, 4) if p99 else None,
        "max_condors": robustness.max_size(p99, capital, SIZING_SHARE),
        "insufficient": bool(dr.get("insufficient")),
    }


def judge(reference: dict, tests: dict[str, dict], holdout: bool) -> None:
    """In place: the reference's verdict on its own net, each challenger's on its paired difference."""
    o = reference["own"]
    if o["n"] < MIN_SESSIONS:
        reference["verdict"], reference["why"] = "no verdict", f"{o['n']} sessions < {MIN_SESSIONS}"
    else:
        checks = {"mean_positive": (o["mean_net"] or 0) > 0, "t_above_bar": (o["t"] or 0) > T_BAR}
        if not holdout:
            checks |= {
                "both_halves_positive": all((x or 0) > 0 for x in o["halves"].values()),
                "survives_1_5x_fees": o["cost_stress"]["survives"],
            }
        reference["checks"] = checks
        reference["verdict"] = "pass" if all(checks.values()) else "fail"
    holm = core_stats.holm({k: v["paired"]["p"] for k, v in tests.items()}, alpha=ALPHA)
    for k, v in tests.items():
        pr = v["paired"]
        v["holm_ok"] = holm[k]
        if pr["n"] < MIN_SESSIONS:
            v["verdict"], v["why"] = "no verdict", f"{pr['n']} sessions < {MIN_SESSIONS}"
            continue
        checks = {"diff_positive": (pr["mean_diff"] or 0) > 0, "t_above_bar": (pr["t"] or 0) > T_BAR}
        if not holdout:
            checks |= {
                "holm": v["holm_ok"],
                "diff_both_halves_positive": all((x or 0) > 0 for x in pr["halves"].values()),
                "own_survives_1_5x_fees": v["own"]["cost_stress"]["survives"],
            }
            if v["width"] > 1:
                mine, ref = v["net_over_bp"], v["reference_net_over_bp"]
                checks["net_over_bp_at_least_reference"] = mine is not None and ref is not None and mine >= ref
        v["checks"] = checks
        v["verdict"] = "pass" if all(checks.values()) else "fail"


def study(settings: dict, window: str, workers: int, capital: float) -> dict:
    cov = out_dir() / "coverage.json"
    why = coverage_refusal(json.loads(cov.read_text(encoding="utf-8")) if cov.exists() else None, window)
    if why:
        raise SystemExit(why)
    holdout = window == "holdout"
    start, end = settings["windows"][window]
    registry = json.loads((home.config_dir() / "meic.risk.json").read_text(encoding="utf-8"))
    reg_path = out_dir() / f"meic.risk.{window}.json"
    reg_path.write_text(json.dumps(study_registry(registry, stress=not holdout), indent=1), encoding="utf-8")
    os.environ["MEIC_RISK_CONFIG"] = str(reg_path)
    arms = list(ARMS) + ([] if holdout else [REFERENCE + HALFWAY_SUFFIX])
    report = runner.run(
        wide(settings),
        arms,
        window,
        module="meic",
        symbol=SYMBOL,
        cadence=CADENCE,
        provisional=True,
        spend_holdout=holdout,
        unvalidated_holdout=holdout,
        workers=workers,
        log=lambda m: print(f"[{SYMBOL}] {m}", file=sys.stderr),
    )
    run_dir = paths.results_dir() / "runs" / report["run_id"]
    rows = ledger_rows(run_dir)
    sess = session_rows(report["run_id"])
    risk = risk_by_session(rows)
    ref_sessions = sess.get(REFERENCE, {})

    def arm_block(arm: str) -> dict:
        mine = sess.get(arm, {})
        return {
            "own": own(mine, start, end),
            "net_over_bp": per_risk(mine, risk.get(arm, {}), sorted(mine)),
            "stopped_share": stopped_share(rows, arm),
            "sizing": sizing(trade_nets(run_dir, arm), mine, capital),
        }

    reference = {"stop": STOPS["s20"], "width": 1, **arm_block(REFERENCE)}
    tests: dict[str, dict] = {}
    for arm in CHALLENGERS:
        stop, width = ARMS[arm]
        mine = sess.get(arm, {})
        both = sorted(set(mine) & set(ref_sessions))
        tests[arm] = {
            "stop": STOPS[stop],
            "width": width,
            **arm_block(arm),
            "paired": paired(mine, ref_sessions, start, end),
            "reference_net_over_bp": per_risk(ref_sessions, risk.get(REFERENCE, {}), both),
        }
        # The arm's own net over buying power over the same sessions as the reference's, for the check.
        tests[arm]["net_over_bp"] = per_risk(mine, risk.get(arm, {}), both)
    judge(reference, tests, holdout)
    reported = {}
    if not holdout:
        half = sess.get(REFERENCE + HALFWAY_SUFFIX, {})
        reported[REFERENCE + HALFWAY_SUFFIX] = {"own": own(half, start, end)}
    doc = {
        "item": ITEM,
        "window": window,
        "span": [start, end],
        "provisional": True,
        "run_id": report["run_id"],
        "refused_sessions": len(report.get("refused") or {}),
        "vix_fallback_sessions": report.get("vix_fallback_sessions"),
        "reference": {REFERENCE: reference},
        "arms": tests,
        "reported": reported,
    }
    path = out_dir() / f"study-{window}-{start}-{end}.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    return {
        "written": str(path),
        REFERENCE: reference.get("verdict"),
        "passing": sorted(a for a, v in tests.items() if v.get("verdict") == "pass"),
    }


# ── kill step ────────────────────────────────────────────────────────────────


def kill_block(trades: list[dict], span: tuple[str, str], runs: list[str]) -> dict:
    """The reference's kill criteria from its own trades, in the live config's `kill` shape."""
    nets = [float(r["net_pnl"]) for r in trades]
    result = killcriteria.derive(nets, sessions=len({r.get("session") for r in trades}) or None)
    evidence = f"meic_ic arm {REFERENCE} on {SYMBOL}, item {ITEM} runs {', '.join(runs)}, {span[0]}..{span[1]}"
    return {"written": date.today().isoformat(), "evidence": evidence, **result}


def kill(settings: dict) -> dict:
    runs, trades = [], []
    for window in ("design", "holdout"):
        start, end = settings["windows"][window]
        f = out_dir() / f"study-{window}-{start}-{end}.json"
        if not f.exists():
            raise SystemExit(f"no {window} study yet: the kill criteria are derived from both windows")
        run_id = json.loads(f.read_text(encoding="utf-8"))["run_id"]
        runs.append(run_id)
        trades += trade_nets(paths.results_dir() / "runs" / run_id, REFERENCE)
    span = (settings["windows"]["design"][0], settings["windows"]["holdout"][1])
    block = kill_block(sorted(trades, key=lambda r: r.get("session") or ""), span, runs)
    path = out_dir() / "kill-criteria.json"
    path.write_text(json.dumps(block, indent=1), encoding="utf-8")
    return {"written": str(path), **block}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--step", required=True, choices=("coverage", "study", "kill"))
    ap.add_argument("--window", choices=("design", "holdout"))
    ap.add_argument("--capital", type=float, help="the account the sizing read is against (study step)")
    ap.add_argument("--workers", type=int, default=default_workers())
    args = ap.parse_args(argv)
    if args.step == "study" and (not args.window or not args.capital or args.capital <= 0):
        ap.error("--step study needs --window and a positive --capital")
    s = _settings.load(None)
    if args.step == "coverage":
        coverage(s)
        out = {"written": str(out_dir() / "coverage.json")}
    elif args.step == "study":
        out = study(s, args.window, args.workers, args.capital)
    else:
        out = kill(s)
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
