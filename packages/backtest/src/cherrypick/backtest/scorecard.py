"""The scorecard: one arm's stats table and plain-English insights, from its per-session series and
(optionally) its trades. Pure; nothing here reads a file, changes a result or records a trial.

Both run cards (`cards.arm_cards`) and research scripts build it the same way, so a module replay and
a study report read alike. A study script passes its own series (`[{session, net, trades}]`, one row
per session in order, a session with no entry at zero) and, where it keeps them, its trades; it writes
the result under its arm's `scorecard` key, where the study report finds it.

**The stats** (`stats`), in whole-position dollars, net of whatever the series is net of:

- `total_pl`, `max_drawdown` (dollars below the running peak, positive), `return_on_dd` (total P/L
  over max drawdown, a ratio, not annualised), `high_pl` / `low_pl` (the highest and lowest the
  cumulative P/L reached, from a start of zero).
- `profit_factor`: gross wins over gross losses, per trade when trades are given, else per session.
- `sharpe` / `sortino`: annualised from sessions (mean over sd, or over downside deviation, times
  the square root of 252). The deflated Sharpe that decides a verdict lives in `stats.py`, per
  session; these are for reading, not deciding.

**The insights** (`insights`) are each one sentence with its numbers: estimated costs and their
share of gross, trades entered at unusually wide spreads, and the drawdowns' lengths in calendar
days (the longest, how many lasted `LONG_DRAWDOWN_DAYS` or more, the average length and depth).
"""

from __future__ import annotations

import math
import statistics
from datetime import date

#: A drawdown lasting this many calendar days or more is counted as long.
LONG_DRAWDOWN_DAYS = 30
#: A trade whose modelled slippage is this many times the arm's median trade slippage entered at an
#: unusually wide spread (slippage is a fixed fraction of the legs' bid-ask, so it scales with it).
WIDE_SPREAD_MULT = 3.0
#: Fewer trades with a recorded slippage than this and the wide-spread insight is left out.
MIN_SLIPPAGE_TRADES = 20
SESSIONS_PER_YEAR = 252


def _r(v: float | None, nd: int = 2) -> float | None:
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(v, nd)


def _day(s: str) -> date:
    return date.fromisoformat(str(s)[:10])


def drawdown_episodes(series: list[dict]) -> list[dict]:
    """Every drawdown, from the session of the peak it fell from to the first session back at or
    above that peak (`recovered` False when the series ends below it). `days` counts calendar days
    between those sessions; `depth` is the deepest it fell, in dollars. The peak starts at zero."""
    out: list[dict] = []
    cum = peak = 0.0
    peak_day = None
    cur: dict | None = None
    for s in series:
        cum += s["net"] or 0.0
        if cum >= peak:
            if cur is not None:
                cur.update(end=s["session"], recovered=True)
                out.append(cur)
                cur = None
            peak, peak_day = cum, s["session"]
            continue
        if cur is None:
            cur = {"start": peak_day or s["session"], "depth": 0.0}
        cur["depth"] = max(cur["depth"], peak - cum)
    if cur is not None and series:
        cur.update(end=series[-1]["session"], recovered=False)
        out.append(cur)
    for d in out:
        d["days"] = (_day(d["end"]) - _day(d["start"])).days
        d["depth"] = _r(d["depth"])
    return out


def _sharpe_sortino(nets: list[float]) -> tuple[float | None, float | None]:
    if len(nets) < 2:
        return None, None
    mean = statistics.fmean(nets)
    sd = statistics.stdev(nets)
    down = math.sqrt(sum(min(x, 0.0) ** 2 for x in nets) / len(nets))
    scale = math.sqrt(SESSIONS_PER_YEAR)
    return (
        _r(mean / sd * scale, 3) if sd > 0 else None,
        _r(mean / down * scale, 3) if down > 0 else None,
    )


def _profit_factor(values: list[float]) -> float | None:
    wins = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    return _r(wins / losses, 2) if losses > 0 else None


def scorecard_stats(series: list[dict], trades: list[dict] | None = None) -> dict:
    nets = [s["net"] or 0.0 for s in series]
    cum = peak = 0.0
    high = low = 0.0
    max_dd = 0.0
    for v in nets:
        cum += v
        peak = max(peak, cum)
        high, low = max(high, cum), min(low, cum)
        max_dd = max(max_dd, peak - cum)
    trade_nets = [t["net_pnl"] for t in trades or [] if t.get("net_pnl") is not None]
    pf_basis = "trades" if trade_nets else "sessions"
    sharpe, sortino = _sharpe_sortino(nets)
    traded = [s for s in series if s.get("trades")]
    return {
        "total_pl": _r(cum),
        "max_drawdown": _r(max_dd),
        "return_on_dd": _r(cum / max_dd, 2) if max_dd > 0 else None,
        "high_pl": _r(high),
        "low_pl": _r(low),
        "profit_factor": _profit_factor(trade_nets if trade_nets else [s["net"] for s in traded]),
        "profit_factor_basis": pf_basis,
        "sharpe": sharpe,
        "sortino": sortino,
        "sessions": len(series),
        "traded_sessions": len(traded),
        "trades": len(trade_nets) if trade_nets else sum(int(s.get("trades") or 0) for s in series),
    }


def _costs(trades: list[dict]) -> dict | None:
    """Recorded fees, settlement and modelled slippage, and their share of gross P/L before them."""
    fees = [t["fees"] for t in trades if t.get("fees") is not None]
    if not fees:
        return None
    settlement = sum(t.get("settlement") or 0.0 for t in trades)
    slippage = sum(t.get("slippage") or 0.0 for t in trades)
    fee_total = sum(fees) + settlement
    gross = sum(t["gross_pnl"] for t in trades if t.get("gross_pnl") is not None)
    return {
        "fees": _r(fee_total),
        "slippage": _r(slippage),
        "share_of_gross": _r(fee_total / gross, 4) if gross > 0 else None,
        "not_recorded": len(trades) - len(fees),
    }


def _wide_spreads(trades: list[dict]) -> dict | None:
    slips = [t["slippage"] for t in trades if (t.get("slippage") or 0) > 0]
    if len(slips) < MIN_SLIPPAGE_TRADES:
        return None
    median = statistics.median(slips)
    wide = [t for t in trades if (t.get("slippage") or 0) > WIDE_SPREAD_MULT * median]
    return {
        "count": len(wide),
        "of": len(slips),
        "median_slippage": _r(median),
        "mult": WIDE_SPREAD_MULT,
        "sessions": sorted({str(t.get("session")) for t in wide if t.get("session")})[:20],
    }


def _money(v: float) -> str:
    return f"-${abs(v):,.0f}" if v < 0 else f"${v:,.0f}"


def insights(series: list[dict], trades: list[dict] | None = None) -> list[dict]:
    """The insight lines, each `{key, text, flag, ...numbers}`. A line whose inputs are missing is
    left out rather than shown empty. `flag` marks what a reader should look at first."""
    lines: list[dict] = []
    trades = trades or []
    costs = _costs(trades)
    if costs is not None:
        share = costs["share_of_gross"]
        text = f"Estimated fees: {_money(costs['fees'])}"
        if share is not None:
            text += f" ({share:.0%} of gross P/L)"
        if costs["slippage"]:
            text += f"; modelled slippage {_money(costs['slippage'])}, already in the fill prices"
        lines.append(
            {"key": "fees", "text": text + ".", "flag": share is not None and share >= 0.25, **costs}
        )
    wide = _wide_spreads(trades)
    if wide is not None and wide["count"]:
        lines.append(
            {
                "key": "wide_spreads",
                "text": f"{wide['count']} of {wide['of']} trades entered at bid/ask spreads over "
                f"{wide['mult']:g}x the usual (slippage over {wide['mult']:g}x the median "
                f"{_money(wide['median_slippage'])}).",
                "flag": wide["count"] / wide["of"] >= 0.05,
                **wide,
            }
        )
    eps = drawdown_episodes(series)
    if eps:
        longest = max(eps, key=lambda d: d["days"])
        long_n = sum(1 for d in eps if d["days"] >= LONG_DRAWDOWN_DAYS)
        avg_days = statistics.fmean(d["days"] for d in eps)
        avg_depth = statistics.fmean(d["depth"] for d in eps)
        tail = "" if longest["recovered"] else ", not recovered by the end"
        lines += [
            {
                "key": "longest_drawdown",
                "text": f"Longest drawdown lasted {longest['days']} days ({longest['start']} to "
                f"{longest['end']}{tail}).",
                "flag": longest["days"] >= 3 * LONG_DRAWDOWN_DAYS,
                **longest,
            },
            {
                "key": "long_drawdowns",
                "text": f"{long_n} drawdown{'' if long_n == 1 else 's'} lasted "
                f"{LONG_DRAWDOWN_DAYS} days or more.",
                "flag": False,
                "count": long_n,
                "days": LONG_DRAWDOWN_DAYS,
            },
            {
                "key": "avg_drawdown",
                "text": f"Average drawdown lasted {avg_days:.0f} days and fell {_money(avg_depth)} "
                f"({len(eps)} drawdowns).",
                "flag": False,
                "avg_days": _r(avg_days, 1),
                "avg_depth": _r(avg_depth),
                "count": len(eps),
            },
        ]
    return lines


def scorecard(series: list[dict], trades: list[dict] | None = None) -> dict:
    """One arm's `{stats, insights}`. `series` is `[{session, net, trades}]` in session order;
    `trades` (optional) carry `net_pnl`, and where recorded `gross_pnl`, `fees`, `settlement`,
    `slippage` and `session` (`core.ledgers`' row shape)."""
    return {"stats": scorecard_stats(series, trades), "insights": insights(series, trades)}
