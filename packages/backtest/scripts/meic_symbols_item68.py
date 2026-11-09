"""Item 68, MEIC's live rule on QQQ and NDX (docs/edge-hypotheses-plan.md): `pm-200` entering once a day,
14:00-14:30, on each symbol at its SPX-equivalent wing and at the width the config gives it today.

    python packages/backtest/scripts/meic_symbols_item68.py --step coverage
    python packages/backtest/scripts/meic_symbols_item68.py --step study --window design|holdout [--workers N]

Runs the backtest's own MEIC replay (`cherrypick.backtest.runner`, MEIC's own engine) over each symbol's
wide 60 s vendor store (`QQQ-60s-wide.db`, `NDX-60s-wide.db`), provisionally: MEIC history has not
unlocked, and these symbols never can (there is no paper record of them to validate against). The
holdout is spent by this run, once (`unvalidated_holdout`). Writes under the backtest data dir
(results/research/item68/), never into the repo: the inputs are licensed.

**The arms, as declared.** Each is the registry's `pm-200` with entries 14:00-14:30 (item 23's
`live-1400`, the rule live trades, gates and all), `symbols` set to the one symbol and
`wing_width_points` set: `q1` and `q5` on QQQ, `n40` and `n10` on NDX. 1 and 40 are SPX's 10 points
scaled by price; 5 and 10 are the widths `wing_widths_by_symbol` gives the symbols today. `control`
runs because the runner requires it, pointed at the symbol, and is never judged.

**The verdict**, per arm, the observation one session (a session with no entry counts, at zero): the
arm's own mean net above zero with a one-sided t above 2, Holm across the four arms, positive in both
time-halves of the window, still positive at 1.5x fees; at least 100 sessions; then the holdout's mean
above zero with t above 2.

**Reported, never judged:** each arm's net over buying power beside SPX `live-1400`'s over the same
sessions (item 23's recorded runs); the correlation of its session nets with SPX's; the two together;
in the design window the fill stress of docs/edge-hypotheses-plan.md's verdict-fill convention
(each arm again halfway from mid to the natural fill, a quarter of every leg's spread); per arm the
entries left open at the session's end, which for QQQ, physically settled, is a close the 15:30
force-close missed.

**The coverage step** reads quotes only, no P&L, and must come first: each symbol's sessions per window,
its strike spacing near spot, and the share of hourly 16-delta shorts whose wing is quoted.
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

from cherrypick.core import home, robustness
from cherrypick.core import progress as core_progress
from cherrypick.core import stats as core_stats
from cherrypick.core.procs import default_workers

from cherrypick.backtest import paths, results, runner, store
from cherrypick.backtest import settings as _settings

ITEM = 68
TAG = "wide"
CADENCE = 60
ET = ZoneInfo("America/New_York")
BASE_ARM = "pm-200"
LIVE = "live-1400"
WINDOW = ("14:00", "14:30")
#: The judged arms per symbol: (arm, wing width). The first is SPX's 10 points scaled by price.
ARMS = {"QQQ": {"q1": 1, "q5": 5}, "NDX": {"n40": 40, "n10": 10}}
#: The stress fill: halfway from mid to the natural fill on every leg, a quarter of its spread (the
#: verdict fill is the model's own eighth).
HALFWAY = {"slippage_frac_of_spread": 0.25}
HALFWAY_SUFFIX = "-halfway"
SHORT_DELTA = 0.16
HOURS = (10, 11, 12, 13, 14, 15)
MIN_SESSIONS = 100
T_BAR = 2.0
ALPHA = 0.05
PASS_STRESS = 1.5
OPEN_STATUSES = ("pending", "open", "partial", "partial_entry")


def out_dir() -> Path:
    d = paths.results_dir() / "research" / f"item{ITEM}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def wide(settings: dict) -> dict:
    return store.tagged(settings, TAG)


def study_registry(registry: dict, symbol: str, *, stress: bool) -> dict:
    """The machine's MEIC registry with this symbol's arms added (and their stress-fill twins in the
    design window), `control` pointed at the symbol so the runner's paired arm can trade it."""
    profiles = dict(registry.get("profiles") or {})
    if not isinstance(profiles.get(BASE_ARM), dict):
        raise SystemExit(f"the MEIC registry has no {BASE_ARM!r} arm to build on")
    live = {k: v for k, v in profiles[BASE_ARM].items() if not k.startswith("_")}
    live.update(
        enabled=True, paper_entry_window_start=WINDOW[0], entry_window_end=WINDOW[1], symbols=[symbol]
    )
    for name, width in ARMS[symbol].items():
        profiles[name] = {**live, "wing_width_points": width}
        if stress:
            profiles[name + HALFWAY_SUFFIX] = {**profiles[name], **HALFWAY}
    control = profiles.get("control")
    if isinstance(control, dict):
        profiles["control"] = {**control, "symbols": [symbol]}
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


def _session_marks(conn: sqlite3.Connection, symbol: str, day: str) -> list[dict]:
    samples = conn.execute(
        "SELECT id, ts FROM gex_quote_samples WHERE symbol = ? AND trade_date = ? ORDER BY ts", (symbol, day)
    ).fetchall()
    out = []
    for hh in HOURS:
        target = datetime.fromisoformat(day).replace(hour=hh, tzinfo=ET).timestamp()
        hit = next(((i, ts) for i, ts in samples if ts >= target), None)
        if hit is None:
            continue
        quotes = conn.execute(
            "SELECT side, strike, delta FROM gex_quotes WHERE sample_id = ?", (hit[0],)
        ).fetchall()
        spot = conn.execute(
            "SELECT spot FROM gex_spot_history WHERE symbol = ? AND trade_date = ? AND ts <= ? "
            "ORDER BY ts DESC LIMIT 1",
            (symbol, day, hit[1]),
        ).fetchone()
        out.append(
            {
                "spacing": spacing(quotes, spot[0] if spot else None),
                **{w: wing_inside(quotes, float(w)) for w in ARMS[symbol].values()},
            }
        )
    return out


def _share(xs: list[bool | None]) -> float | None:
    known = [x for x in xs if x is not None]
    return round(sum(known) / len(known), 4) if known else None


def coverage(settings: dict) -> dict:
    s = wide(settings)
    doc: dict = {"item": ITEM, "step": "coverage", "symbols": {}}
    with core_progress.Reporter("item68-coverage") as rep:
        rep.phase("stores", total=len(ARMS) * len(settings["windows"]), unit="symbol-windows")
        for symbol in ARMS:
            path = store.db_path(s, symbol, CADENCE)
            per: dict = {"store": str(path)}
            for window, (start, end) in settings["windows"].items():
                row: dict = {"span": [start, end], "sessions": 0}
                if path.exists():
                    conn = store.connect(s, symbol, CADENCE)
                    try:
                        days = [d for d, _ in runner.sessions_in(conn, symbol, start, end)]
                        row["sessions"] = len(days)
                        row["first"], row["last"] = (days[0], days[-1]) if days else (None, None)
                        marks = [m for d in days for m in _session_marks(conn, symbol, d)]
                        gaps = [m["spacing"] for m in marks if m["spacing"]]
                        row["hourly_marks"] = len(marks)
                        row["strike_spacing_median"] = statistics.median(gaps) if gaps else None
                        for w in ARMS[symbol].values():
                            row[f"w{w}_put_wing_inside"] = _share([m[w]["put"] for m in marks])
                            row[f"w{w}_call_wing_inside"] = _share([m[w]["call"] for m in marks])
                    finally:
                        conn.close()
                per[window] = row
                rep.advance("stores", current=f"{symbol} {window}")
            doc["symbols"][symbol] = per
    doc["note"] = (
        f"share of hourly {SHORT_DELTA:g}-delta shorts ({HOURS[0]}:00-{HOURS[-1]}:00) whose wing is "
        "quoted in the same sample, per judged width; strike spacing within 1% of spot; quotes only, no P&L"
    )
    (out_dir() / "coverage.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


def coverage_refusal(doc: dict | None, window: str) -> str | None:
    """Why the study may not run yet: no coverage read, or a symbol with no session in the window."""
    if not doc:
        return "run --step coverage first: each store's reach is reported before any P&L"
    empty = [
        sym for sym in ARMS if not ((doc.get("symbols") or {}).get(sym, {}).get(window) or {}).get("sessions")
    ]
    if empty:
        return f"no {window} session in the coverage read for {', '.join(empty)}: fetch, then re-run coverage"
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
        "win_rate": round(sum(x > 0 for x in nets) / len(nets), 3) if nets else None,
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
    """Every entry the run's per-session ledgers hold: arm, session, width, credit, quantity, status."""
    out = []
    for db in sorted(ledger_dir.glob("*.db")):
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            for r in conn.execute(
                "SELECT arm, trade_date, wing_width, net_credit, quantity, status FROM ic_trades"
            ):
                out.append(dict(r))
        except sqlite3.OperationalError:
            continue  # a ledger the replay never initialised holds no entry
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


def per_risk(sessions: dict[str, dict], risk: dict[str, float], days: list[str]) -> float | None:
    """Summed net over the summed `(width - credit) x 100 x quantity` of every entry, over `days`."""
    held = sum(risk.get(d, 0.0) for d in days)
    return round(sum(sessions[d]["net"] for d in days if d in sessions) / held, 5) if held > 0 else None


def correlation(a: dict[str, dict], b: dict[str, dict]) -> dict:
    days = sorted(set(a) & set(b))
    xs, ys = [a[d]["net"] for d in days], [b[d]["net"] for d in days]
    try:
        r = round(statistics.correlation(xs, ys), 3) if len(days) >= 3 else None
    except statistics.StatisticsError:
        r = None  # a constant series (an arm that never entered) has no correlation
    return {"n": len(days), "r": r}


def spx_reference(window: str) -> tuple[dict[str, dict], dict[str, float], str | None]:
    """Item 23's recorded SPX `live-1400` sessions for the window, its buying power, and the run id."""
    files = sorted((paths.results_dir() / "research" / "item23").glob(f"study-{window}-*.json"))
    if not files:
        return {}, {}, None
    run_id = json.loads(files[-1].read_text(encoding="utf-8")).get("run_id")
    if not run_id:
        return {}, {}, None
    risk = risk_by_session(ledger_rows(paths.results_dir() / "runs" / run_id)).get(LIVE, {})
    return session_rows(run_id).get(LIVE, {}), risk, run_id


def judge(tests: dict[str, dict], holdout: bool) -> None:
    holm = core_stats.holm({k: v["own"]["p"] for k, v in tests.items()}, alpha=ALPHA)
    for k, v in tests.items():
        o = v["own"]
        v["holm_ok"] = holm[k]
        if o["n"] < MIN_SESSIONS:
            v["verdict"], v["why"] = "no verdict", f"{o['n']} sessions < {MIN_SESSIONS}"
            continue
        checks = {"mean_positive": (o["mean_net"] or 0) > 0, "t_above_bar": (o["t"] or 0) > T_BAR}
        if not holdout:
            checks |= {
                "holm": v["holm_ok"],
                "both_halves_positive": all((x or 0) > 0 for x in o["halves"].values()),
                "survives_1_5x_fees": o["cost_stress"]["survives"],
            }
        v["checks"] = checks
        v["verdict"] = "pass" if all(checks.values()) else "fail"


def run_symbol(settings: dict, symbol: str, window: str, workers: int) -> dict:
    """One run of `symbol`'s arms over `window`; what the judge and the reports read from it."""
    holdout = window == "holdout"
    registry = json.loads((home.config_dir() / "meic.risk.json").read_text(encoding="utf-8"))
    reg_path = out_dir() / f"meic.risk.{symbol}.json"
    reg_path.write_text(
        json.dumps(study_registry(registry, symbol, stress=not holdout), indent=1), encoding="utf-8"
    )
    os.environ["MEIC_RISK_CONFIG"] = str(reg_path)
    arms = list(ARMS[symbol]) + ([] if holdout else [a + HALFWAY_SUFFIX for a in ARMS[symbol]])
    report = runner.run(
        wide(settings),
        arms,
        window,
        module="meic",
        symbol=symbol,
        cadence=CADENCE,
        provisional=True,
        spend_holdout=holdout,
        unvalidated_holdout=holdout,
        workers=workers,
        log=lambda m: print(f"[{symbol}] {m}", file=sys.stderr),
    )
    rows = ledger_rows(paths.results_dir() / "runs" / report["run_id"])
    return {
        "report": report,
        "sessions": session_rows(report["run_id"]),
        "risk": risk_by_session(rows),
        "left_open": {
            a: sum(1 for r in rows if r["arm"] == a and r["status"] in OPEN_STATUSES) for a in arms
        },
    }


def study(settings: dict, window: str, workers: int) -> dict:
    cov = out_dir() / "coverage.json"
    why = coverage_refusal(json.loads(cov.read_text(encoding="utf-8")) if cov.exists() else None, window)
    if why:
        raise SystemExit(why)
    holdout = window == "holdout"
    start, end = settings["windows"][window]
    spx, spx_risk, spx_run = spx_reference(window)
    tests, reported, runs = {}, {}, {}
    for symbol in ARMS:
        got = run_symbol(settings, symbol, window, workers)
        runs[symbol] = {
            "run_id": got["report"]["run_id"],
            "refused_sessions": len(got["report"].get("refused") or {}),
            "vix_fallback_sessions": got["report"].get("vix_fallback_sessions"),
        }
        for arm in ARMS[symbol]:
            mine = got["sessions"].get(arm, {})
            both = sorted(set(mine) & set(spx))
            tests[arm] = {
                "symbol": symbol,
                "width": ARMS[symbol][arm],
                "own": own(mine, start, end),
                "left_open_at_end": got["left_open"].get(arm, 0),
                "net_over_bp": per_risk(mine, got["risk"].get(arm, {}), sorted(mine)),
                "beside_spx": {
                    "sessions": len(both),
                    "arm_net_over_bp": per_risk(mine, got["risk"].get(arm, {}), both),
                    "spx_net_over_bp": per_risk(spx, spx_risk, both),
                    "correlation": correlation(mine, spx),
                    "together": own(
                        {d: {k: mine[d][k] + spx[d][k] for k in ("net", "fees", "trades")} for d in both},
                        start,
                        end,
                    ),
                },
            }
            if not holdout:
                half = got["sessions"].get(arm + HALFWAY_SUFFIX, {})
                reported[arm + HALFWAY_SUFFIX] = {
                    "own": own(half, start, end),
                    "net_over_bp": per_risk(half, got["risk"].get(arm + HALFWAY_SUFFIX, {}), sorted(half)),
                }
    judge(tests, holdout)
    doc = {
        "item": ITEM,
        "window": window,
        "span": [start, end],
        "provisional": True,
        "runs": runs,
        "spx_live_1400_run_id": spx_run,
        "spx_live_1400": own(spx, start, end) if spx else None,
        "arms": tests,
        "reported": reported,
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
    ap.add_argument("--step", required=True, choices=("coverage", "study"))
    ap.add_argument("--window", choices=("design", "holdout"))
    ap.add_argument("--workers", type=int, default=default_workers())
    args = ap.parse_args(argv)
    if args.step == "study" and not args.window:
        ap.error("--step study needs --window")
    s = _settings.load(None)
    out = coverage(s) if args.step == "coverage" else study(s, args.window, args.workers)
    print(
        json.dumps(out if args.step == "study" else {"written": str(out_dir() / "coverage.json")}, indent=1)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
