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
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path

from cherrypick.core import ledgers as core_ledgers
from cherrypick.core import metrics as core_metrics

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


def arm_cards(series: list[dict], trades: list[dict], capital: float | None) -> dict:
    path = equity_path(series)
    periods = drawdown_periods(path)
    acct = None if capital is None else account(path, capital)
    if acct is not None:
        for d in periods:
            peak_equity = capital + d["peak_cum"]
            d["depth_pct"] = _r(d["depth"] / peak_equity, 4) if peak_equity > 0 else None
    return {
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
    }


def cards(
    series_by_arm: dict[str, list[dict]],
    trades_by_arm: dict[str, dict[str, list[dict]]],
    capital: float | None = None,
) -> dict:
    """Every arm's cards. `trades_by_arm` is {arm: {session: [trade, ...]}}. A chosen capital is one
    account for every arm, so arms read side by side; with none, each arm reads against its own peak
    session risk (an arm whose schema records none has no account cards). Not one shared peak: a
    run's widest arm, MEIC's control sampling stream, would set every other arm's account."""
    arms = {}
    for arm, series in series_by_arm.items():
        by_session = trades_by_arm.get(arm, {})
        risks = [r for rows in by_session.values() if (r := session_risk(rows)) is not None]
        peak_risk = max(risks) if risks else None
        out = arm_cards(series, [t for rows in by_session.values() for t in rows], capital or peak_risk)
        out["peak_risk"] = _r(peak_risk)
        arms[arm] = out
    return {
        "account": {"capital": _r(capital), "basis": "chosen" if capital is not None else "peak_risk"},
        "arms": arms,
    }


# ------------------------------------------------------------------------------------------- reading


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def for_run(run_id: str, schema: str, arms: list[str], capital: float | None = None) -> dict:
    """Read one run's per-session results and its kept ledgers, and build its cards."""
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
    trades_by_arm = _ledger_trades(run_id, schema, list(series_by_arm), sessions)
    return cards(series_by_arm, trades_by_arm, capital)


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
        finally:
            lconn.close()
        # The runner's own attribution: an arm's rows that settled in this session.
        for r in rows:
            if r["session"] == day and r["arm"] in trades_by_arm:
                trades_by_arm[r["arm"]].setdefault(day, []).append(r)
    return trades_by_arm


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
