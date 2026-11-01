"""The run cards: what a completed run looked like over time, read from what the runner already stored.

Pure functions over a run's per-session results (`session_results`) and the replayed ledgers the
runner keeps beside its report (`results/runs/<run_id>/<day>.db`, read through `core.ledgers`, the
one home of every schema's net rule), plus `for_run`, which reads them. Nothing here changes a result
or records a trial: these are views of a run, served to the operator's own console and nowhere else.

**The account is a lens, not a replay.** The runner replays fixed lots, so every dollar figure is
what it is whatever the account. Given a starting capital, the account cards restate the same
dollars against it: equity = capital + cumulative net, never compounded (sizing as a share of
equity would change which trades happen, which is a different replay and a different trial). The
default capital is each arm's own peak capital at risk: the most any one of its sessions held at
risk (MEIC's condors' max losses summed, flies' recorded book peak risk), so a percentage reads as
return on what that arm needed. A chosen capital is one account for every arm. A schema that records
neither has no default, and the account cards are absent until a capital is chosen.

Sessions are the unit throughout (`stats.py`'s rule): a drawdown's length is counted in sessions,
and the one annualised figure is the account's simple annual return (return / years), named so.

**Health checks** (`health`, 2026-11-01) put the warnings a reader would otherwise assemble from the
cards first, each against a bar declared here: the result rests on its best few sessions, few
sessions win, a long losing run, a long drawdown, a decaying edge, heavy fees. Description only:
a flag never changes a result or a verdict.
"""

from __future__ import annotations

import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path

from cherrypick.core import ledgers as core_ledgers
from cherrypick.core import metrics as core_metrics
from cherrypick.core import robustness

from . import paths, stats

TOP_DRAWDOWNS = 5
#: Fewer sessions than this and the edge trend gives no verdict: two halves of a handful say nothing.
MIN_TREND_SESSIONS = 20
NOT_RECORDED = "not recorded"


def _r(v: float | None, nd: int = 2) -> float | None:
    return None if v is None else round(v, nd)


def equity_path(series: list[dict]) -> list[dict]:
    """`series` is [{session, net, trades}] in session order. Each point gains the cumulative net and
    the drawdown (dollars below the running peak, >= 0). The peak starts at zero: a run that loses
    from its first session is in drawdown from its first session."""
    out, cum, peak = [], 0.0, 0.0
    for s in series:
        cum += s["net"]
        peak = max(peak, cum)
        out.append({"session": s["session"], "net": _r(s["net"]), "cum": _r(cum), "drawdown": _r(peak - cum)})
    return out


def drawdown_periods(path: list[dict], top: int = TOP_DRAWDOWNS) -> list[dict]:
    """The deepest drawdowns, each from the peak it fell from to the session it got back there.
    `peak` is None for a drawdown that began before the first session closed (the run's start)."""
    periods: list[dict] = []
    peak_val, peak_i, peak_session = 0.0, -1, None
    cur: dict | None = None
    for i, p in enumerate(path):
        if p["cum"] >= peak_val:
            if cur is not None:
                cur["recovered"] = p["session"]
                cur["sessions_to_recover"] = i - cur["_trough_i"]
                periods.append(cur)
                cur = None
            peak_val, peak_i, peak_session = p["cum"], i, p["session"]
            continue
        depth = peak_val - p["cum"]
        if cur is None:
            cur = {"peak": peak_session, "peak_cum": _r(peak_val), "depth": 0.0, "_peak_i": peak_i}
        if depth > cur["depth"]:
            cur.update(depth=_r(depth), trough=p["session"], _trough_i=i)
    if cur is not None:
        cur["recovered"] = None
        cur["sessions_to_recover"] = None
        periods.append(cur)
    for d in periods:
        d["sessions_to_trough"] = d.pop("_trough_i") - d.pop("_peak_i")
    return sorted(periods, key=lambda d: -d["depth"])[:top]


def _max_drawdown(series: list[dict]) -> float:
    return max((p["drawdown"] for p in equity_path(series)), default=0.0)


def calendar(series: list[dict]) -> dict:
    """Net by month ("YYYY-MM"), and a row per year: net, sessions, sessions traded, winning
    sessions, and the year's own max drawdown (its path started from zero on its first session)."""
    months: dict[str, float] = defaultdict(float)
    by_year: dict[str, list[dict]] = defaultdict(list)
    for s in series:
        months[s["session"][:7]] += s["net"]
        by_year[s["session"][:4]].append(s)
    years = []
    for year, rows in sorted(by_year.items()):
        traded = [s for s in rows if s["trades"] > 0]
        years.append(
            {
                "year": year,
                "net": _r(sum(s["net"] for s in rows)),
                "sessions": len(rows),
                "traded": len(traded),
                "winning": sum(1 for s in traded if s["net"] > 0),
                "max_drawdown": _r(_max_drawdown(rows)),
            }
        )
    return {"months": {k: _r(v) for k, v in sorted(months.items())}, "years": years}


def _anatomy(nets: list[float]) -> dict:
    wins = [v for v in nets if v > 0]
    losses = [v for v in nets if v < 0]
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None
    return {
        "count": len(nets),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": _r(len(wins) / len(nets), 4) if nets else None,
        "avg_win": _r(avg_win),
        "avg_loss": _r(avg_loss),
        "payoff_ratio": _r(avg_win / -avg_loss, 2) if avg_win is not None and avg_loss else None,
        "best": _r(max(nets)) if nets else None,
        "worst": _r(min(nets)) if nets else None,
    }


def session_anatomy(series: list[dict]) -> dict:
    """Over the sessions that traded (a session with no trade is neither a win nor a loss): the win
    rate, average winning and losing session, payoff ratio, the best and worst session and when, and
    the shape of the distribution (skew, and kurtosis where a normal reads 3)."""
    traded = [s for s in series if s["trades"] > 0]
    out = _anatomy([s["net"] for s in traded])
    out["best_session"] = max(traded, key=lambda s: s["net"])["session"] if traded else None
    out["worst_session"] = min(traded, key=lambda s: s["net"])["session"] if traded else None
    m = stats.moments([s["net"] for s in traded])
    out["skew"] = None if m is None else _r(m["skew"], 3)
    out["kurtosis"] = None if m is None else _r(m["kurtosis"], 3)
    return out


def trade_anatomy(trades: list[dict]) -> dict:
    """The same per trade. A row whose net was never recorded is counted apart, never as a flat trade."""
    nets = [t["net_pnl"] for t in trades if t.get("net_pnl") is not None]
    return {**_anatomy(nets), "not_recorded": len(trades) - len(nets)}


def premium(trades: list[dict]) -> dict | None:
    """What the trades collected and kept, over those that carry a credit (`entry_credit`): premium
    collected, trading fees, fee drag (fees / premium, settlement apart, the suite's definition) and
    capture rate (`core.metrics.capture_rate`: net over the structures' own ceilings). None when no
    trade carries a credit (flies: a legged book has no single credit to name)."""
    credited = [t for t in trades if t.get("entry_credit") and t.get("net_pnl") is not None]
    if not credited:
        return None
    collected = sum(t["entry_credit"] for t in credited)
    fees = [t["fees"] for t in credited if t.get("fees") is not None]
    capture = core_metrics.capture_rate(trades)
    return {
        "n": len(credited),
        "premium": _r(collected),
        "fees": _r(sum(fees)),
        "fees_not_recorded": len(credited) - len(fees),
        "fee_drag": _r(sum(fees) / collected, 4) if collected > 0 else None,
        "capture_rate": capture["value"],
        "capture_n": capture["n"],
    }


def exits(trades: list[dict]) -> list[dict] | None:
    """How trades ended (`outcome`, in the module's own words), each with its count, share, win rate,
    average and total net. None when the schema records no outcome at all."""
    if not trades or all(t.get("outcome") is None for t in trades):
        return None
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        groups[t.get("outcome") or NOT_RECORDED].append(t)
    out = []
    for outcome, rows in groups.items():
        nets = [t["net_pnl"] for t in rows if t.get("net_pnl") is not None]
        out.append(
            {
                "outcome": outcome,
                "count": len(rows),
                "share": _r(len(rows) / len(trades), 4),
                "win_rate": _r(sum(1 for v in nets if v > 0) / len(nets), 4) if nets else None,
                "avg_net": _r(sum(nets) / len(nets)) if nets else None,
                "total_net": _r(sum(nets)),
            }
        )
    return sorted(out, key=lambda e: -e["count"])


def edge_trend(series: list[dict]) -> dict | None:
    """Mean net per session in the run's first half against its second, with a block-bootstrap
    interval on the change. "within noise" when the interval spans zero; otherwise the edge is
    "improving" or "decaying". None under MIN_TREND_SESSIONS sessions."""
    values = [s["net"] for s in series]
    if len(values) < MIN_TREND_SESSIONS:
        return None
    half = len(values) // 2
    first, second = values[:half], values[half:]
    ci = stats.block_bootstrap_diff_ci(first, second)
    mean_first = sum(first) / len(first)
    mean_second = sum(second) / len(second)
    verdict = None
    if ci is not None:
        verdict = "improving" if ci[0] > 0 else "decaying" if ci[1] < 0 else "within noise"
    return {
        "split": series[half]["session"],
        "mean_first": _r(mean_first),
        "mean_second": _r(mean_second),
        "change": _r(mean_second - mean_first),
        "ci95": None if ci is None else [_r(ci[0]), _r(ci[1])],
        "verdict": verdict,
    }


def session_risk(trades: list[dict]) -> float | None:
    """What one arm's session held at risk: flies' recorded peak risk per book (summed over the
    session's books), else the positions' own max losses summed. None when neither is recorded."""
    books = {t["book_id"]: t["book_peak_risk"] for t in trades if t.get("book_peak_risk") is not None}
    if books:
        return sum(books.values())
    caps = [t["capital"] for t in trades if t.get("capital") is not None]
    return sum(caps) if caps else None


def stop_session_risk(trades: list[dict]) -> float | None:
    """What one arm's session would lose if every position's stops fired at their triggers
    (`stop_risk`, MEIC's condors). None unless every position records one: a session half sized by
    its stops and half by nothing is not a session's risk."""
    risks = [t.get("stop_risk") for t in trades]
    return sum(risks) if risks and all(r is not None for r in risks) else None


#: How the compounded lens measures a unit's risk: the positions' max loss, or their stop distance.
RISK_UNITS = {"max_loss": session_risk, "stop": stop_session_risk}


def account(path: list[dict], capital: float) -> dict | None:
    """The same dollars against a starting capital, never compounded: return, simple annual return
    over the calendar span the sessions cover, max drawdown as a share of peak equity, and their
    ratio (annual return / max drawdown). `ruined_on` is the first session the equity reached zero:
    an account that small would have stopped there, and every figure past it describes money that
    was not there."""
    if not path or capital <= 0:
        return None
    eq_peak, worst, ruined_on = capital, 0.0, None
    for p in path:
        eq = capital + p["cum"]
        eq_peak = max(eq_peak, eq)
        worst = max(worst, (eq_peak - eq) / eq_peak if eq_peak > 0 else 0.0)
        if eq <= 0 and ruined_on is None:
            ruined_on = p["session"]
    total = path[-1]["cum"]
    days = (date.fromisoformat(path[-1]["session"]) - date.fromisoformat(path[0]["session"])).days + 1
    years = days / 365.25
    ret = total / capital
    annual = ret / years if years > 0 else None
    return {
        "capital": _r(capital),
        "ending": _r(capital + total),
        "return": _r(ret, 4),
        "years": _r(years, 2),
        "annual_return": _r(annual, 4),
        "max_drawdown": _r(worst, 4),
        "return_to_drawdown": _r(annual / worst, 2) if annual is not None and worst > 0 else None,
        "ruined_on": ruined_on,
    }


#: The compounded lens's default share of equity put at risk each session, in percent.
DEFAULT_RISK_PCT = 2.0
#: The sizing check's defaults: the drawdown a size may reach at the 99th percentile, as a share of the
#: account, in percent; and the overshoot percentile the stop-plus-overshoot risk is read at.
DEFAULT_DD_PCT = 30.0
OVERSHOOT_QUANTILE = 0.95


def _mode_summary(start: float, path: list[float], years: float | None) -> dict:
    end = path[-1] if path else start
    peak, worst_d, worst_p = start, 0.0, 0.0
    for eq in path:
        peak = max(peak, eq)
        worst_d = max(worst_d, peak - eq)
        worst_p = max(worst_p, (peak - eq) / peak if peak > 0 else 0.0)
    cagr = None
    if years and years > 0:
        cagr = -1.0 if end <= 0 else (end / start) ** (1 / years) - 1
    return {
        "ending": _r(end),
        "cagr": _r(cagr, 4),
        "max_drawdown": _r(worst_d),
        "max_drawdown_pct": _r(worst_p, 4),
    }


def compounded(
    series: list[dict],
    by_session: dict[str, list[dict]],
    start: float,
    risk_pct: float = DEFAULT_RISK_PCT,
    unit: str = "max_loss",
) -> dict | None:
    """A LENS, not what the replay ran, and never an input to a verdict: the same sessions re-sized
    each session to put `risk_pct` percent of the equity at that point at risk. A unit is the set of
    lots the replay held that session, its risk the session's own, known at entry, so sizing never
    borrows a later session's: `unit` "max_loss" reads the positions' max losses (`session_risk`),
    "stop" what their stops would lose at the trigger (`stop_session_risk`; a gap loses more). Each
    session's net is scaled by the units, which assumes P&L, fills and per-contract fees scale with size.

    Two curves: whole units, floored (how it would trade; a small account can floor to none, a session
    then left out), and fractional (the upper bound). A session that traded with no recorded risk cannot
    be sized and is left out of both, counted. An account that reaches zero stops there."""
    if not series or start <= 0 or risk_pct <= 0:
        return None
    measure = RISK_UNITS[unit]
    share = risk_pct / 100.0
    eq = {"whole": start, "fractional": start}
    stopped = {"whole": None, "fractional": None}
    units_whole: list[int] = []
    path: list[dict] = []
    unsized = rounded_to_none = 0
    for s in series:
        day, net = s["session"], s["net"]
        risk = measure(by_session.get(day, []))
        if s.get("trades") and (risk is None or risk <= 0):
            unsized += 1
        elif s.get("trades"):
            for mode in eq:
                if stopped[mode] is not None:
                    continue
                units = eq[mode] * share / risk
                if mode == "whole":
                    units = math.floor(units)
                    units_whole.append(units)
                    if units == 0:
                        rounded_to_none += 1
                eq[mode] += net * units
                if eq[mode] <= 0:
                    eq[mode], stopped[mode] = 0.0, day
        path.append({"session": day, "whole": _r(eq["whole"]), "fractional": _r(eq["fractional"])})
    days = (date.fromisoformat(series[-1]["session"]) - date.fromisoformat(series[0]["session"])).days + 1
    years = days / 365.25
    traded = [u for u in units_whole if u > 0]
    return {
        "start": _r(start),
        "risk_pct": risk_pct,
        "unit": unit,
        "years": _r(years, 2),
        "whole": {
            **_mode_summary(start, [p["whole"] for p in path], years),
            "stopped_on": stopped["whole"],
            "units_min": min(traded) if traded else 0,
            "units_max": max(traded) if traded else 0,
            "sessions_rounded_to_none": rounded_to_none,
        },
        "fractional": {
            **_mode_summary(start, [p["fractional"] for p in path], years),
            "stopped_on": stopped["fractional"],
        },
        "sessions_unsized": unsized,
        "path": path,
    }


#: The health checks' bars. A session win rate under this means long losing runs are normal.
HEALTH_WIN_RATE = 0.25
#: This many losing sessions in a row (sessions with no trade neither break nor extend a run).
HEALTH_LOSING_RUN = 10
#: A drawdown lasting this many sessions from its peak to its recovery, or to the run's end.
HEALTH_DRAWDOWN_SESSIONS = 60
#: Trading fees at this share of the premium collected.
HEALTH_FEE_DRAG = 0.25


def longest_losing_run(series: list[dict]) -> int:
    """The most traded sessions in a row that lost. A session with no trade is skipped: it is neither
    a win nor a loss, so it neither breaks a run nor extends it."""
    best = run = 0
    for s in series:
        if not s["trades"]:
            continue
        run = run + 1 if s["net"] < 0 else 0
        best = max(best, run)
    return best


def longest_drawdown(path: list[dict]) -> dict:
    """The drawdown that lasted longest, in sessions from its peak to the session it got back (or to
    the run's last session, `recovered` False). Zero sessions when the run never fell below a peak."""
    longest = {"sessions": 0, "recovered": True}
    start = None
    peak = 0.0
    for i, p in enumerate(path):
        if p["cum"] >= peak:
            if start is not None and i - start > longest["sessions"]:
                longest = {"sessions": i - start, "recovered": True}
            peak, start = p["cum"], None
        elif start is None:
            start = i - 1
    if start is not None and len(path) - 1 - start > longest["sessions"]:
        longest = {"sessions": len(path) - 1 - start, "recovered": False}
    return longest


def health(series: list[dict], out: dict) -> dict:
    """The arm's health checks from its own cards (`out`, as `arm_cards` built them) and series. Each
    check is {check, flag, bar, ...its numbers}; a check without the numbers it needs is left out, not
    passed. Description only."""
    checks = []
    best = robustness.best_sessions({s["session"]: s["net"] for s in series if s["trades"]})
    if not best.get("insufficient"):
        checks.append(
            {
                "check": "best_sessions",
                "flag": best["rests_on_best"],
                "bar": "the other sessions average above zero",
                "k": best["k"],
                "share": best["share"],
                "rest_mean": best["rest_mean"],
            }
        )
    win = out["sessions"].get("win_rate")
    if win is not None:
        checks.append(
            {"check": "win_rate", "flag": win < HEALTH_WIN_RATE, "bar": HEALTH_WIN_RATE, "value": win}
        )
    losing = longest_losing_run(series)
    checks.append(
        {
            "check": "losing_run",
            "flag": losing >= HEALTH_LOSING_RUN,
            "bar": HEALTH_LOSING_RUN,
            "value": losing,
        }
    )
    dd = longest_drawdown(out["equity"])
    checks.append(
        {
            "check": "drawdown_length",
            "flag": dd["sessions"] >= HEALTH_DRAWDOWN_SESSIONS,
            "bar": HEALTH_DRAWDOWN_SESSIONS,
            "value": dd["sessions"],
            "recovered": dd["recovered"],
        }
    )
    trend = out.get("edge_trend")
    if trend is not None and trend.get("verdict") is not None:
        checks.append(
            {
                "check": "edge_trend",
                "flag": trend["verdict"] == "decaying",
                "bar": "the second half's interval is not below the first",
                "value": trend["verdict"],
            }
        )
    drag = (out.get("premium") or {}).get("fee_drag")
    if drag is not None:
        checks.append(
            {"check": "fee_drag", "flag": drag >= HEALTH_FEE_DRAG, "bar": HEALTH_FEE_DRAG, "value": drag}
        )
    return {"flags": sum(1 for c in checks if c["flag"]), "checks": checks}


def arm_cards(
    series: list[dict],
    trades: list[dict],
    capital: float | None,
    by_session: dict[str, list[dict]] | None = None,
    risk_pct: float = DEFAULT_RISK_PCT,
    risk_unit: str = "max_loss",
) -> dict:
    path = equity_path(series)
    periods = drawdown_periods(path)
    acct = None if capital is None else account(path, capital)
    if acct is not None:
        for d in periods:
            peak_equity = capital + d["peak_cum"]
            d["depth_pct"] = _r(d["depth"] / peak_equity, 4) if peak_equity > 0 else None
    out = {
        "equity": path,
        "max_drawdown": _r(max((p["drawdown"] for p in path), default=0.0)),
        "drawdowns": periods,
        "calendar": calendar(series),
        "sessions": session_anatomy(series),
        "trades": trade_anatomy(trades),
        "premium": premium(trades),
        "exits": exits(trades),
        "edge_trend": edge_trend(series),
        "account": acct,
        "compounded": None
        if capital is None
        else compounded(series, by_session or {}, capital, risk_pct, risk_unit),
    }
    out["health"] = health(series, out)
    return out


def cards(
    series_by_arm: dict[str, list[dict]],
    trades_by_arm: dict[str, dict[str, list[dict]]],
    capital: float | None = None,
    risk_pct: float = DEFAULT_RISK_PCT,
    risk_unit: str = "max_loss",
    dd_pct: float = DEFAULT_DD_PCT,
) -> dict:
    """Every arm's cards. `trades_by_arm` is {arm: {session: [trade, ...]}}. A chosen capital is one
    account for every arm, so arms read side by side; with none, each arm reads against its own peak
    session risk (an arm whose schema records none has no account cards). Not one shared peak: a
    run's widest arm, MEIC's control sampling stream, would set every other arm's account. The sizing
    check reads against a chosen capital only: sizing to the arm's own peak risk answers nothing."""
    arms = {}
    for arm, series in series_by_arm.items():
        by_session = trades_by_arm.get(arm, {})
        risks = [r for rows in by_session.values() if (r := session_risk(rows)) is not None]
        peak_risk = max(risks) if risks else None
        trades = [t for rows in by_session.values() for t in rows]
        out = arm_cards(series, trades, capital or peak_risk, by_session, risk_pct, risk_unit)
        out["peak_risk"] = _r(peak_risk)
        out["sizing"] = None if capital is None else sizing(series, by_session, capital, dd_pct)
        arms[arm] = out
    return {
        "account": {
            "capital": _r(capital),
            "basis": "chosen" if capital is not None else "peak_risk",
            "risk_pct": risk_pct,
            "risk_unit": risk_unit,
            "dd_pct": dd_pct,
        },
        "arms": arms,
    }


#: The arm every other arm's settings are read against.
SETTINGS_BASE = "control"


def arm_settings(specs: dict[str, dict | None], base: str = SETTINGS_BASE) -> dict[str, dict]:
    """Each arm's settings strip from its spec (`runner.*_spec_for`): cadence, fill model, and every
    param that differs from `base`'s, as {param: {"arm", "base"}}, so a grid arm reads as the one
    setting it tests. A spec that was never recorded reads `recorded: False`, never as no difference.
    `base` is None for the base arm itself, or where the base's own spec is not recorded."""
    ref = (specs.get(base) or {}).get("params")
    out: dict[str, dict] = {}
    for arm, spec in specs.items():
        if not spec:
            out[arm] = {"recorded": False}
            continue
        params = spec.get("params") or {}
        compare = arm != base and isinstance(ref, dict)
        differs = {}
        if compare:
            for k in sorted(set(params) | set(ref)):
                if params.get(k) != ref.get(k):
                    differs[k] = {"arm": params.get(k), "base": ref.get(k)}
        out[arm] = {
            "recorded": True,
            "cadence": spec.get("cadence"),
            "fill_model": spec.get("fill_model"),
            "base": base if compare else None,
            "differs": differs,
        }
    return out


# ------------------------------------------------------------------------------------------- reading


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def for_run(
    run_id: str,
    schema: str,
    arms: list[str],
    capital: float | None = None,
    risk_pct: float = DEFAULT_RISK_PCT,
    risk_unit: str = "max_loss",
    dd_pct: float = DEFAULT_DD_PCT,
) -> dict:
    """Read one run's per-session results and its kept ledgers, and build its cards."""
    series_by_arm, trades_by_arm = arm_inputs(run_id, schema, arms)
    return cards(series_by_arm, trades_by_arm, capital, risk_pct, risk_unit, dd_pct)


def arm_inputs(
    run_id: str, schema: str, arms: list[str]
) -> tuple[dict[str, list[dict]], dict[str, dict[str, list[dict]]]]:
    """What the cards are built from, for a study script to build its own views on: each arm's
    per-session series ([{session, net, trades}], in session order) and its trades by session, read
    from the run's results and the ledgers it kept. Arms the run did not record read as empty."""
    conn = _ro(paths.results_db())
    try:
        series_by_arm: dict[str, list[dict]] = {a: [] for a in arms}
        for r in conn.execute(
            "SELECT arm, session, net, trades FROM session_results WHERE run_id = ? ORDER BY session",
            (run_id,),
        ):
            series_by_arm.setdefault(r["arm"], []).append(
                {"session": r["session"], "net": r["net"] or 0.0, "trades": r["trades"] or 0}
            )
    finally:
        conn.close()
    sessions = sorted({s["session"] for series in series_by_arm.values() for s in series})
    return series_by_arm, _ledger_trades(run_id, schema, list(series_by_arm), sessions)


def _ledger_trades(
    run_id: str, schema: str, arms: list[str], sessions: list[str]
) -> dict[str, dict[str, list[dict]]]:
    """{arm: {session: [trade, ...]}} from the ledgers a run keeps, read through `core.ledgers`."""
    reader = core_ledgers.READERS[schema]
    ledger_dir = paths.results_dir() / "runs" / run_id
    trades_by_arm: dict[str, dict[str, list[dict]]] = {a: {} for a in arms}
    for day in sessions:
        db = ledger_dir / f"{day}.db"
        if not db.exists():
            continue
        lconn = _ro(db)
        try:
            rows = reader(lconn)
            stops = _meic_stop_risk(lconn) if schema == "meic_ic" else {}
            detail = _meic_stop_detail(lconn) if schema == "meic_ic" else {}
        finally:
            lconn.close()
        # The runner's own attribution: an arm's rows that settled in this session.
        for r in rows:
            if r["session"] == day and r["arm"] in trades_by_arm:
                if stops:
                    r = {**r, "stop_risk": stops.get(r.get("position_id"))}
                if detail:
                    r = {**r, **detail.get(r.get("position_id"), {})}
                trades_by_arm[r["arm"]].setdefault(day, []).append(r)
    return trades_by_arm


def _meic_stop_risk(conn: sqlite3.Connection) -> dict[str, float]:
    """Each MEIC condor's loss if both sides stop at their trigger, by its order id: a side stops when
    its cost to close reaches `stop_trigger_original` x the condor's credit, so both stopping loses
    (2 x ratio - 1) x credit, in whole-position dollars. The stop model's own figure: a gap through the
    trigger loses more. A condor without a recorded trigger or credit is left out (None)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ic_trades)")}
    if not {"ic_order_id", "net_credit", "stop_trigger_original"} <= cols:
        return {}
    qty = "quantity" if "quantity" in cols else "1"
    mult = "dollar_multiplier" if "dollar_multiplier" in cols else "100"
    out = {}
    for oid, credit, ratio, q, m in conn.execute(
        f"SELECT ic_order_id, net_credit, stop_trigger_original, {qty}, {mult} FROM ic_trades"
    ):
        if oid is None or credit is None or ratio is None or credit <= 0:
            continue
        risk = (2 * float(ratio) - 1) * float(credit) * float(m or 100) * float(q or 1)
        if risk > 0:
            out[oid] = round(risk, 2)
    return out


def _meic_stop_detail(conn: sqlite3.Connection) -> dict[str, dict]:
    """Each MEIC condor's stop terms and how far its stopped sides filled past their trigger, by order
    id: `stop_ratio`, `credit_dollars` (the credit in whole-position dollars) and `overshoots`, one
    stopped side's cost to close over its trigger level (>= 1; 1.29 is 29% past it). The sizing
    check's stop-plus-overshoot risk is read from these."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ic_trades)")}
    need = {"ic_order_id", "net_credit", "stop_trigger_original", "put_stop_cost", "call_stop_cost"}
    have_legs = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'ic_spread_legs'").fetchone()
    if not need <= cols or not have_legs:
        return {}
    qty = "quantity" if "quantity" in cols else "1"
    mult = "dollar_multiplier" if "dollar_multiplier" in cols else "100"
    stopped: dict[str, set[str]] = defaultdict(set)
    for oid, side in conn.execute(
        "SELECT ic_order_id, side FROM ic_spread_legs WHERE exit_reason = 'per_side_stop'"
    ):
        stopped[oid].add(side)
    out = {}
    for oid, credit, ratio, q, m, put_cost, call_cost in conn.execute(
        f"SELECT ic_order_id, net_credit, stop_trigger_original, {qty}, {mult}, "
        "put_stop_cost, call_stop_cost FROM ic_trades"
    ):
        if oid is None or credit is None or ratio is None or credit <= 0 or ratio <= 0:
            continue
        trigger = float(ratio) * float(credit)
        costs = {"put": put_cost, "call": call_cost}
        out[oid] = {
            "stop_ratio": float(ratio),
            "credit_dollars": float(credit) * float(m or 100) * float(q or 1),
            "overshoots": [
                round(costs[s] / trigger, 4) for s in sorted(stopped.get(oid, ())) if costs.get(s)
            ],
        }
    return out


def _quantile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def sizing(
    series: list[dict], by_session: dict[str, list[dict]], account: float, dd_pct: float = DEFAULT_DD_PCT
) -> dict | None:
    """A SIZING CHECK for one arm, never a verdict: the most whole units of the replay's lots whose
    99th-percentile drawdown over the bootstrap's horizon (`core.robustness.drawdown_range`, the B1
    kill-criteria bootstrap) stays within `dd_pct` percent of `account` (`robustness.max_size`), and what
    that size risks a session. A unit's risk is read two ways: its max loss, and for MEIC its stops
    filled at the trigger plus the `OVERSHOOT_QUANTILE` of how far recorded stops filled past it (both
    sides stopped, capped at the max loss). The sessions' median of each is reported, as a share of
    the account at the recommended size."""
    if not series or account <= 0 or dd_pct <= 0:
        return None
    nets = [s["net"] for s in series if s.get("trades")]
    dd = robustness.drawdown_range(nets, sessions=len(series))
    out: dict = {"account": _r(account), "dd_pct": dd_pct}
    if dd.get("insufficient"):
        return {
            **out,
            "insufficient": True,
            "evidence": dd.get("evidence_trades"),
            "min_evidence": dd.get("min_evidence"),
        }
    units = robustness.max_size(dd["drawdown_block"], account, dd_pct / 100.0)
    trades = [t for rows in by_session.values() for t in rows]
    overshoots = [o for t in trades for o in (t.get("overshoots") or [])]
    tail = _quantile(overshoots, OVERSHOOT_QUANTILE)
    max_loss, stop_tail = [], []
    for rows in by_session.values():
        if (ml := session_risk(rows)) is not None:
            max_loss.append(ml)
        if tail is not None and rows and all(t.get("stop_ratio") is not None for t in rows):
            per = [
                min(t.get("capital") or math.inf, (2 * t["stop_ratio"] * tail - 1) * t["credit_dollars"])
                for t in rows
            ]
            stop_tail.append(sum(per))
    ml_med = statistics.median(max_loss) if max_loss else None
    st_med = statistics.median(stop_tail) if stop_tail else None
    return {
        **out,
        "insufficient": False,
        "units": units,
        "drawdown_p99_per_unit": _r(dd["drawdown_block"]),
        "horizon_sessions": dd["horizon"],
        "evidence_sessions": len(nets),
        "overshoot": None
        if not overshoots
        else {
            "n": len(overshoots),
            "p50": _r(_quantile(overshoots, 0.5), 3),
            "p95": _r(_quantile(overshoots, 0.95), 3),
            "p99": _r(_quantile(overshoots, 0.99), 3),
        },
        "unit_risk_max_loss": _r(ml_med),
        "unit_risk_stop_tail": _r(st_med),
        "risk_pct_max_loss": None if ml_med is None else _r(100 * units * ml_med / account, 2),
        "risk_pct_stop_tail": None if st_med is None else _r(100 * units * st_med / account, 2),
    }


#: A trade's fields the trade log shows, as `core.ledgers` names them. Nothing is computed here.
TRADE_LOG_FIELDS = (
    "session",
    "entry_time",
    "exit_time",
    "outcome",
    "gross_pnl",
    "fees",
    "settlement",
    "slippage",
    "net_pnl",
    "entry_credit",
    "capital",
    "position_id",
)


def trade_log(by_session: dict[str, list[dict]]) -> list[dict]:
    """One arm's trades, oldest first by session then exit time: each trade's own recorded fields,
    a field the schema does not record as None (never zero)."""
    rows = [{k: t.get(k) for k in TRADE_LOG_FIELDS} for day in sorted(by_session) for t in by_session[day]]
    return sorted(rows, key=lambda t: (t["session"] or "", t["exit_time"] or ""))


def trades_for_run(run_id: str, schema: str, arm: str) -> list[dict]:
    """One arm's trade log, read from the run's per-session results and the ledgers it kept."""
    conn = _ro(paths.results_db())
    try:
        sessions = [
            r["session"]
            for r in conn.execute(
                "SELECT session FROM session_results WHERE run_id = ? AND arm = ? ORDER BY session",
                (run_id, arm),
            )
        ]
    finally:
        conn.close()
    return trade_log(_ledger_trades(run_id, schema, [arm], sessions)[arm])


def parse_capital(raw: str | None) -> float | None:
    """A chosen capital: a positive, finite number of dollars, or None for the default."""
    if raw is None or raw == "":
        return None
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"capital must be a positive number of dollars, not {raw!r}")
    return value


def parse_risk_pct(raw: str | None) -> float:
    """The compounded lens's risk per session, in percent: above 0 and at most 100, else the default."""
    if raw is None or raw == "":
        return DEFAULT_RISK_PCT
    value = float(raw)
    if not math.isfinite(value) or not 0 < value <= 100:
        raise ValueError(f"risk percent must be above 0 and at most 100, not {raw!r}")
    return value


def parse_risk_unit(raw: str | None) -> str:
    """The compounded lens's risk unit (`RISK_UNITS`), else max loss."""
    if raw is None or raw == "":
        return "max_loss"
    if raw not in RISK_UNITS:
        raise ValueError(f"risk unit must be one of {', '.join(RISK_UNITS)}, not {raw!r}")
    return raw
