"""Readings over a DAILY series -- an account's NAV, or a book's marked equity in dollars.

The bundle in `cherrypick.core.metrics` reads closed trades. That is the right basis for a module
that opens and closes many positions, and the wrong one for two that the suite now runs:

- contango holds one fund per arm and switches about 19 times a year. Its trades are few and say
  nothing about the drawdown inside a stint; its NAV, marked every session, is what it is judged on.
- curve holds a spread for weeks. A closed-trade drawdown only sees the loss when the trade ends;
  the daily marked equity sees it while it is open.

So these read a dated series, one point per session. Every count here is in DAYS, never trades,
and each reading says so (`basis`), so a "sample" of 40 days is never mistaken for 40 trades.

`nav_reading` is for a series that compounds (a NAV); `equity_reading` for a dollar P&L path that
does not (curve's marked equity, which has no account to compound in). Both take
`[(iso_date, value), ...]` in date order. None never means zero: a metric whose minimum sample is
not met reads None, the bundle's rule.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from datetime import date

from cherrypick.core.metrics import CVAR_MIN_SESSIONS, CVAR_QUANTILE, MIN_EFFECTIVE_N, probabilistic_sharpe

PERIODS_PER_YEAR = 252


def daily_returns(navs: Sequence[tuple[str, float]]) -> list[float]:
    """Simple returns between consecutive points. A non-positive NAV ends the series there: a return
    off zero is undefined, and a wiped-out account has no later returns to measure."""
    out = []
    for (_, a), (_, b) in zip(navs, navs[1:], strict=False):
        if a <= 0:
            break
        out.append(b / a - 1)
    return out


def _moments(values: Sequence[float]) -> tuple[float, float, float, float] | None:
    """(mean, per-observation Sharpe, skew, raw kurtosis), or None on a flat or short series."""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    m2 = sum((v - mean) ** 2 for v in values) / n
    if m2 <= 0:
        return None
    sr = mean / math.sqrt(m2 * n / (n - 1))
    skew = (sum((v - mean) ** 3 for v in values) / n) / m2**1.5
    kurt = (sum((v - mean) ** 4 for v in values) / n) / m2**2
    return mean, sr, skew, kurt


def min_track_record(values: Sequence[float], benchmark: float = 0.0, confidence: float = 0.95) -> int | None:
    """Minimum Track Record Length (Bailey & López de Prado, 2012): how many observations a series
    with this Sharpe, skew and kurtosis needs before its Sharpe beats `benchmark` at `confidence`.

        MinTRL = 1 + (1 - skew*SR + (kurt - 1)/4 * SR^2) * (z / (SR - SR*))^2

    None when the observed Sharpe does not exceed the benchmark (no length is enough), on a flat or
    short series, or where the moments make the variance term non-positive."""
    m = _moments(values)
    if m is None:
        return None
    _, sr, skew, kurt = m
    if sr <= benchmark:
        return None
    var_term = 1 - skew * sr + (kurt - 1) / 4 * sr**2
    if var_term <= 0:
        return None
    z = statistics.NormalDist().inv_cdf(confidence)
    return math.ceil(1 + var_term * (z / (sr - benchmark)) ** 2)


def _drawdowns(levels: Sequence[float]) -> list[float]:
    """Each point's fall below its running peak, as a fraction of that peak (0 at a new high)."""
    out, peak = [], -math.inf
    for v in levels:
        peak = max(peak, v)
        out.append(v / peak - 1 if peak > 0 else 0.0)
    return out


def _span(below: Sequence[bool]) -> dict:
    """Longest run of points below the peak, and the run still open at the end (days)."""
    longest = current = 0
    for b in below:
        current = current + 1 if b else 0
        longest = max(longest, current)
    return {"longest": longest, "open": current}


def monthly_returns(navs: Sequence[tuple[str, float]]) -> dict[str, float]:
    """{"YYYY-MM": return} from each month's last point against the previous month's last (the
    first month against the series' first point). The heat map's input."""
    if len(navs) < 2:
        return {}
    last: dict[str, float] = {}
    for d, v in navs:
        last[d[:7]] = v
    out, prev = {}, navs[0][1]
    for month, v in last.items():
        if prev > 0:
            out[month] = round(v / prev - 1, 6)
        prev = v
    return out


def _tail(values: Sequence[float]) -> float | None:
    n = len(values)
    if n < CVAR_MIN_SESSIONS:
        return None
    k = max(1, int(n * CVAR_QUANTILE))
    return sum(sorted(values)[:k]) / k


def nav_reading(navs: Sequence[tuple[str, float]], periods_per_year: int = PERIODS_PER_YEAR) -> dict:
    """The tear sheet for a compounding series. Annualised figures assume `periods_per_year` points
    a year; ratios that need a sample (Sortino, PSR, MinTRL) refuse below `MIN_EFFECTIVE_N` days,
    CVaR below `CVAR_MIN_SESSIONS`."""
    navs = [(d, float(v)) for d, v in navs if v is not None]
    rets = daily_returns(navs)
    out: dict = {
        "basis": "daily",
        "days": len(rets),
        "start": navs[0][0] if navs else None,
        "end": navs[-1][0] if navs else None,
    }
    if len(navs) < 2:
        return out
    first, final = navs[0][1], navs[-1][1]
    years = (date.fromisoformat(navs[-1][0]) - date.fromisoformat(navs[0][0])).days / 365.25
    total = final / first - 1 if first > 0 else None
    cagr = (final / first) ** (1 / years) - 1 if years > 0 and first > 0 and final > 0 else None
    dds = _drawdowns([v for _, v in navs])
    mdd = min(dds)
    downside = [r for r in rets if r < 0]
    vol = statistics.pstdev(rets) * math.sqrt(periods_per_year) if len(rets) > 1 else None
    enough = len(rets) >= MIN_EFFECTIVE_N
    down_dev = math.sqrt(sum(r * r for r in downside) / len(rets)) if rets else 0.0
    ann = math.sqrt(periods_per_year)
    mean = sum(rets) / len(rets) if rets else 0.0
    m = _moments(rets)
    out.update(
        {
            "total_return": round(total, 6) if total is not None else None,
            "cagr": round(cagr, 6) if cagr is not None else None,
            "vol": round(vol, 6) if vol is not None else None,
            "sharpe": round(mean / statistics.pstdev(rets) * ann, 4) if enough and m is not None else None,
            "sortino": round(mean / down_dev * ann, 4) if enough and down_dev > 0 else None,
            "max_drawdown": round(mdd, 6),
            "mar": round(cagr / -mdd, 4) if cagr is not None and mdd < 0 else None,
            "ulcer_index": round(math.sqrt(sum((100 * d) ** 2 for d in dds) / len(dds)), 4),
            "drawdown_span": _span([d < 0 for d in dds]),
            "worst_day": round(min(rets), 6) if rets else None,
            "best_day": round(max(rets), 6) if rets else None,
            "cvar": round(_tail(rets), 6) if _tail(rets) is not None else None,
            "skew": round(m[2], 4) if m is not None and enough else None,
            "kurtosis": round(m[3], 4) if m is not None and enough else None,
            "psr": probabilistic_sharpe(rets),
            "min_track_record_days": min_track_record(rets) if enough else None,
            "monthly": monthly_returns(navs),
        }
    )
    return out


def equity_reading(equity: Sequence[tuple[str, float]]) -> dict:
    """The tear sheet for a dollar P&L path that does not compound (cumulative marked P&L): drawdown
    and day figures in dollars, the ratios over daily dollar changes. Nothing here is annualised or a
    percentage -- there is no account for it to be a percentage of.

    The path starts from an implicit 0 before its first point, so the first session's own P&L is a
    day like any other: a trade opened and closed on day one is in `net`, not lost as a baseline."""
    equity = [(d, float(v)) for d, v in equity if v is not None]
    levels = [0.0] + [v for _, v in equity]
    changes = [b - a for a, b in zip(levels, levels[1:], strict=False)]
    out: dict = {
        "basis": "daily",
        "days": len(changes),
        "start": equity[0][0] if equity else None,
        "end": equity[-1][0] if equity else None,
    }
    if not changes:
        return out
    peak, mdd, below = 0.0, 0.0, []
    for _, v in equity:
        peak = max(peak, v)
        mdd = min(mdd, v - peak)
        below.append(v < peak)
    m = _moments(changes)
    enough = len(changes) >= MIN_EFFECTIVE_N
    monthly: dict[str, float] = {}
    prev = 0.0
    last: dict[str, float] = {}
    for d, v in equity:
        last[d[:7]] = v
    for month, v in last.items():
        monthly[month] = round(v - prev, 2)
        prev = v
    tail = _tail(changes)
    out.update(
        {
            "net": round(equity[-1][1], 2),
            "max_drawdown": round(mdd, 2),
            "drawdown_span": _span(below),
            "worst_day": round(min(changes), 2),
            "best_day": round(max(changes), 2),
            "cvar": round(tail, 2) if tail is not None else None,
            "sharpe_daily": round(m[1], 4) if m is not None and enough else None,
            "psr": probabilistic_sharpe(changes),
            "min_track_record_days": min_track_record(changes) if enough else None,
            "monthly": monthly,
        }
    )
    return out
