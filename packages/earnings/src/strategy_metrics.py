"""Shared metrics for the strategy-testing program (see
docs/strategy-testing-plan.md). Pure functions over trade-dict lists,
consumed by both strategy_report.py (text) and strategy_dashboard.py
(HTML/matplotlib) so the two can never report different numbers for the
same data.

Trade-level, not period-return, statistics throughout: each earnings play
is a discrete round-trip (open once before close, close once after next
open), not a continuously-held position, so "win rate" here means percent
of trades with positive net P&L -- not percent of positive calendar days
(the QuantStats/period-return convention, which doesn't fit this book).

Cost-adjusted ("net") P&L = trades.pnl - entry_cost - exit_cost. trades.pnl
itself stays gross (see strategy_test_runner.py's docstring) so cost
impact is visible on its own, not silently baked into a number every other
reader of the trades table has always assumed was gross.
"""

import json
import math
import sqlite3
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PAPER_DB_PATH = _DATA_DIR / "paper_trades.db"
LIVE_DB_PATH = _DATA_DIR / "earnings_trades.db"

# The database load_closed_trades (and the dashboard's own rejection-histogram query)
# reads. Defaults to paper so existing importers are unchanged; the strategy_report.py /
# strategy_dashboard.py CLIs reassign it via db_path_for_mode() before reading, so a
# --mode live run points every read at earnings_trades.db atomically.
DB_PATH = PAPER_DB_PATH


def db_path_for_mode(mode: str, db_override: str | None = None) -> Path:
    """Resolve which trades DB a --mode flag selects: an explicit --db override wins,
    else 'paper' -> paper_trades.db / 'live' -> earnings_trades.db. Raises on an unknown
    mode rather than silently defaulting, so a typo never quietly serves the wrong book
    (the whole point of the flag is not confusing live-money data with simulated)."""
    if db_override is not None:
        return Path(db_override)
    if mode == "paper":
        return PAPER_DB_PATH
    if mode == "live":
        return LIVE_DB_PATH
    raise ValueError(f"unknown mode {mode!r} -- expected 'paper' or 'live'")


DIRECTIONAL_SAMPLE_TARGET = 30
SIGNIFICANT_SAMPLE_TARGET = 100

BENCHMARKS = {
    "profit_factor_min": 1.5,
    "max_drawdown_max_pct": 0.20,
    "sharpe_min": 1.0,
    "expectancy_cost_multiple_min": 2.0,
}


def load_closed_trades(profile: str | None = None, strategy: str | None = None, since: str | None = None) -> list[dict]:
    """Closed trades (dicts, parsed entry_context) ordered by closed_at,
    optionally filtered by profile/strategy/since (a scan_date-style
    'YYYY-MM-DD' or ISO timestamp string compared against opened_at's date).
    Read-only, direct SQLite access (not the db_paper.py CLI) since
    reporting/dashboard code runs many of these per invocation.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        query = "SELECT * FROM trades WHERE closed_at IS NOT NULL"
        params: list = []
        if profile:
            query += " AND profile = ?"
            params.append(profile)
        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
        rows = conn.execute(query + " ORDER BY closed_at", params).fetchall()
    finally:
        conn.close()

    trades = []
    for row in rows:
        t = dict(row)
        if t.get("entry_context"):
            try:
                t["entry_context"] = json.loads(t["entry_context"])
            except (TypeError, ValueError):
                t["entry_context"] = None
        if since and t.get("opened_at"):
            from datetime import datetime, date as _date
            opened_date = datetime.fromtimestamp(t["opened_at"]).date()
            since_date = _date.fromisoformat(since) if len(since) == 10 else datetime.fromisoformat(since).date()
            if opened_date < since_date:
                continue
        trades.append(t)
    return trades


def net_pnl(trade: dict) -> float:
    gross = trade.get("pnl") or 0.0
    return gross - (trade.get("entry_cost") or 0.0) - (trade.get("exit_cost") or 0.0)


def iv_crush(trade: dict) -> float | None:
    """entry_iv - exit_iv for one trade -- positive means IV fell (the
    expected post-earnings crush), negative means it actually rose. Both
    are the average IV of this trade's Sell-to-Open leg(s) specifically
    (see strategy_test_runner.py's _avg_sold_iv), fetched live from
    tastytrade's option-chain greeks at entry and exit. None if either
    side's IV wasn't captured (e.g. greeks were briefly unavailable) --
    a missing measurement, not a measured zero."""
    entry_iv = trade.get("entry_iv")
    exit_iv = trade.get("exit_iv")
    if entry_iv is None or exit_iv is None:
        return None
    return entry_iv - exit_iv


def avg_iv_crush(trades: list[dict]) -> dict:
    """Average IV crush across `trades`, plus how many trades actually had
    both entry_iv and exit_iv captured (the denominator can be smaller
    than len(trades) if greeks were unavailable for some trades)."""
    crushes = [c for c in (iv_crush(t) for t in trades) if c is not None]
    if not crushes:
        return {"avg_crush": None, "sample_count": 0}
    return {"avg_crush": sum(crushes) / len(crushes), "sample_count": len(crushes)}


def win_rate(trades: list[dict]) -> float | None:
    pnls = [net_pnl(t) for t in trades]
    if not pnls:
        return None
    wins = [p for p in pnls if p > 0]
    return len(wins) / len(pnls)


def profit_factor(trades: list[dict]) -> float | None:
    pnls = [net_pnl(t) for t in trades]
    gains = sum(p for p in pnls if p > 0)
    losses = abs(sum(p for p in pnls if p < 0))
    if losses == 0:
        return None if gains == 0 else math.inf
    return gains / losses


def expectancy(trades: list[dict]) -> float | None:
    """Average net P&L per trade -- the plain per-trade expected value,
    already cost-adjusted since net_pnl() subtracts entry/exit cost."""
    pnls = [net_pnl(t) for t in trades]
    if not pnls:
        return None
    return sum(pnls) / len(pnls)


def avg_cost_per_trade(trades: list[dict]) -> float | None:
    costs = [(t.get("entry_cost") or 0.0) + (t.get("exit_cost") or 0.0) for t in trades]
    if not costs:
        return None
    return sum(costs) / len(costs)


def sharpe(trades: list[dict]) -> float | None:
    """Trade-level Sharpe: mean(net P&L) / stdev(net P&L). Not annualized
    against a risk-free rate or trading-day count -- these are discrete
    overnight event trades, not a daily return series, so the standard
    annualization factor (sqrt(252)) doesn't apply. This is a relative
    risk-adjusted-return signal for comparing strategies against each
    other, not a textbook annualized Sharpe ratio."""
    pnls = [net_pnl(t) for t in trades]
    if len(pnls) < 2:
        return None
    mean = sum(pnls) / len(pnls)
    variance = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
    stdev = math.sqrt(variance)
    if stdev == 0:
        return None
    return mean / stdev


def equity_curve(trades: list[dict]) -> list[tuple[float, float]]:
    """[(closed_at, cumulative_net_pnl), ...] ordered by close time."""
    curve = []
    running = 0.0
    for t in sorted(trades, key=lambda x: x.get("closed_at") or 0):
        running += net_pnl(t)
        curve.append((t.get("closed_at") or 0.0, running))
    return curve


def max_drawdown(trades: list[dict], capital_basis: float | None = None) -> dict:
    """Largest peak-to-trough decline in the cumulative net-P&L equity
    curve. Returns {"absolute": $, "pct": fraction} -- pct is relative to
    `capital_basis` if given (e.g. the profile's available_capital_paper_
    mode), otherwise relative to the peak equity itself (a self-referential
    fallback when no external capital basis is supplied)."""
    curve = equity_curve(trades)
    if not curve:
        return {"absolute": 0.0, "pct": 0.0}

    peak = 0.0
    worst_abs = 0.0
    for _, cum in curve:
        peak = max(peak, cum)
        worst_abs = min(worst_abs, cum - peak)

    worst_abs = abs(worst_abs)
    if capital_basis:
        pct = worst_abs / capital_basis
    else:
        pct = (worst_abs / peak) if peak > 0 else 0.0
    return {"absolute": round(worst_abs, 2), "pct": round(pct, 4)}


def avg_hold_seconds(trades: list[dict]) -> float | None:
    holds = [
        t["closed_at"] - t["opened_at"]
        for t in trades
        if t.get("closed_at") and t.get("opened_at")
    ]
    if not holds:
        return None
    return sum(holds) / len(holds)


def sample_progress(trades: list[dict]) -> dict:
    n = len(trades)
    return {
        "count": n,
        "directional_target": DIRECTIONAL_SAMPLE_TARGET,
        "significant_target": SIGNIFICANT_SAMPLE_TARGET,
        "directional_met": n >= DIRECTIONAL_SAMPLE_TARGET,
        "significant_met": n >= SIGNIFICANT_SAMPLE_TARGET,
    }


def regime_buckets(trades: list[dict]) -> dict:
    """Bucket trades by IV/RV ratio and realized-move dispersion band, from
    each trade's stored entry_context, so a strategy's sample can be
    checked against the regimes it's actually designed for (e.g. iron_fly
    wants high IV/RV + low dispersion)."""
    def iv_band(ivrv):
        if ivrv is None:
            return "unknown"
        if ivrv < 0.75:
            return "low (<0.75)"
        if ivrv < 1.00:
            return "medium (0.75-1.00)"
        return "high (>=1.00)"

    def dispersion_band(disp):
        if disp is None:
            return "unknown"
        if disp < 0.10:
            return "tight (<0.10)"
        if disp < 0.20:
            return "normal (0.10-0.20)"
        return "wide (>=0.20)"

    buckets: dict[str, int] = {}
    for t in trades:
        ctx = t.get("entry_context") or {}
        key = f"{iv_band(ctx.get('iv_rv_ratio'))} / {dispersion_band(ctx.get('dispersion'))}"
        buckets[key] = buckets.get(key, 0) + 1
    return buckets


def core_five(trades: list[dict], capital_basis: float | None = None) -> dict:
    """The core-five benchmark set (see docs/strategy-testing-plan.md
    research notes): profit factor, max drawdown, Sharpe, win rate,
    expectancy -- plus each metric's pass/fail against BENCHMARKS.
    `pass` is None (not evaluated) for any metric that's None (e.g. too few
    trades for a stdev), never silently treated as a failure."""
    pf = profit_factor(trades)
    mdd = max_drawdown(trades, capital_basis)
    sh = sharpe(trades)
    wr = win_rate(trades)
    exp = expectancy(trades)
    avg_cost = avg_cost_per_trade(trades)

    exp_pass = None
    if exp is not None and avg_cost is not None and avg_cost > 0:
        exp_pass = exp > BENCHMARKS["expectancy_cost_multiple_min"] * avg_cost

    return {
        "win_rate": {"value": wr, "pass": None},
        "profit_factor": {"value": pf, "pass": None if pf is None else pf > BENCHMARKS["profit_factor_min"]},
        "max_drawdown": {"value": mdd, "pass": mdd["pct"] < BENCHMARKS["max_drawdown_max_pct"]},
        "sharpe": {"value": sh, "pass": None if sh is None else sh > BENCHMARKS["sharpe_min"]},
        "expectancy": {"value": exp, "avg_cost": avg_cost, "pass": exp_pass},
    }


def winrate_backtest_agreement(paper_win_rate: float | None, backtest_win_rate: float | None, tolerance: float = 0.15) -> dict:
    """Whether paper win rate roughly agrees with the historical
    scanner.compute_winrate backtest for the same strategy/symbols --
    flags a strategy whose live paper behavior has drifted from its
    backtested edge."""
    if paper_win_rate is None or backtest_win_rate is None:
        return {"agree": None, "diff": None}
    diff = abs(paper_win_rate - backtest_win_rate)
    return {"agree": diff <= tolerance, "diff": round(diff, 4)}


def strategy_summary(trades: list[dict], capital_basis: float | None = None) -> dict:
    """Full per-strategy summary bundle: sample progress, core-five, avg
    hold, regime coverage, equity curve, IV crush. The one function
    strategy_report.py and strategy_dashboard.py should both call per
    strategy."""
    return {
        "sample": sample_progress(trades),
        "core_five": core_five(trades, capital_basis),
        "avg_hold_seconds": avg_hold_seconds(trades),
        "regime_buckets": regime_buckets(trades),
        "equity_curve": equity_curve(trades),
        "iv_crush": avg_iv_crush(trades),
        "total_trades": len(trades),
    }
