"""Phase 5 of docs/strategy-analysis-plan.md: how a set of arms behaves held together.

One arm per strategy -- the arms within a module are alternatives, not holdings -- each read as its
settled net per session (`core.metrics.session_nets_dated`, the series the P&L calendar draws).
Everything here is a pure function of those series; nothing is fitted.

- **Correlation, two ways.** `zero_filled`: every market period inside both arms' spans, a period an
  arm closed nothing counted as zero. `similarity`: only periods both closed something. The two
  answer different questions -- "do they lose together" against "when both trade, do they move
  together" -- and a pair can read near zero on one and high on the other.
- **Effective number of arms**: (sum of each arm's standard deviation / the standard deviation of
  their sum) squared, over the window all of them span. N for N independent arms of equal size, 1
  for arms that are one bet.
- **Drawdown benefit of combining**: 1 - the combined curve's max drawdown / the sum of each arm's.
- **Worst portfolio periods**, with how many arms lost in each, and **each arm's contribution**: its
  net, its share, and its net during the combined curve's worst drawdown.

Two limits are stated with every result rather than hidden:

- No ledger records whether an arm was RUNNING on a day it closed nothing (`core.compare` makes the
  same point), so "zero" is only assumed inside an arm's own span, from its first close to its last.
- Multi-day modules (bwb, calendars, pmcc, curve) book P&L on the CLOSE date, so a daily correlation
  partly measures their exit calendars. `period="week"` sums each arm by ISO week, which mostly
  removes that.

Every figure carries the number of periods behind it, and one under `MIN_PERIODS` is `thin`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, timedelta

from cherrypick.core import calendar as _cal
from cherrypick.core import metrics as _metrics

#: Under this many periods a figure is marked thin: shown, never read as a finding.
MIN_PERIODS = 20
WORST_PERIODS = 5


def _period_of(session: str, period: str) -> str:
    if period == "week":
        d = date.fromisoformat(session[:10])
        return (d - timedelta(days=d.weekday())).isoformat()
    return session[:10]


def market_periods(start: str, end: str, period: str = "day") -> list[str]:
    """Every NYSE session in [start, end] (core.calendar), or every week containing one."""
    d, last = date.fromisoformat(start), date.fromisoformat(end)
    out: list[str] = []
    while d <= last:
        if _cal.is_trading_day(d):
            key = _period_of(d.isoformat(), period)
            if not out or out[-1] != key:
                out.append(key)
        d += timedelta(days=1)
    return out


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson correlation; None under three points or where either side never varies."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    return round(sxy / math.sqrt(sxx * syy), 4)


def _sd(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _drawdown_episode(values: Sequence[float]) -> tuple[int, int] | None:
    """(peak index, trough index) of the deepest peak-to-trough fall of the running sum; the peak is
    the last index BEFORE the fall starts (-1 when the fall starts at the first value)."""
    running, peak, peak_i, best = 0.0, 0.0, -1, (0.0, None)
    for i, v in enumerate(values):
        running += v
        if running > peak:
            peak, peak_i = running, i
        if peak - running > best[0]:
            best = (peak - running, (peak_i, i))
    return best[1]


def portfolio(
    series: Mapping[str, Mapping[str, float]],
    *,
    start: str,
    end: str | None = None,
    period: str = "day",
    weights: str = "equal_risk",
    min_periods: int = MIN_PERIODS,
) -> dict:
    """The portfolio reading of `series` ({arm key: {session: net}}) over [start, end].

    An arm's span runs from its first close to the end of the window: an arm that holds for weeks
    (contango) or closes rarely (curve) is running between its closes, and a span ending at its last
    close would empty the window every other arm shares. Before its span an arm did not exist and
    contributes nothing.

    `weights`: `equal_risk` (the default) scales each arm to the median arm's per-period standard
    deviation over its own span, so the combined figures are not one large book's (MEIC's paper
    control takes ~270 condors a session and books six figures where flies books four); `booked`
    sums the dollars as booked. Correlations are scale-free and the same either way."""
    if period not in ("day", "week"):
        raise ValueError(f"period must be 'day' or 'week', got {period!r}")
    if weights not in ("equal_risk", "booked"):
        raise ValueError(f"weights must be 'equal_risk' or 'booked', got {weights!r}")
    if end is None:  # the latest close any arm made: a session still trading is not a zero
        end = max((s[:10] for nets in series.values() for s in nets), default=start)
    periods = market_periods(start, end, period)
    index = {p: i for i, p in enumerate(periods)}
    by: dict[str, dict[str, float]] = {}
    for key, nets in series.items():
        agg: dict[str, float] = {}
        for session, net in nets.items():
            p = _period_of(session, period)
            if p in index and net is not None:
                agg[p] = agg.get(p, 0.0) + float(net)
        by[key] = agg
    keys = [k for k in series if by[k]]
    first = {k: min(by[k]) for k in keys}

    def span(ks) -> list[str]:
        if not ks:
            return []
        lo = max(first[k] for k in ks)
        return [p for p in periods if p >= lo]

    own = {k: [by[k].get(p, 0.0) for p in span([k])] for k in keys}
    sds = {k: _sd(own[k]) for k in keys}
    positive = sorted(s for s in sds.values() if s > 0)
    median_sd = positive[len(positive) // 2] if positive else 0.0
    scale = {k: (median_sd / sds[k] if weights == "equal_risk" and sds[k] > 0 else 1.0) for k in keys}

    arms = []
    for k in series:
        arms.append(
            {
                "key": k,
                "periods_traded": len(by[k]),
                "first": first.get(k),
                "periods_in_span": len(own.get(k, [])),
                "net": round(sum(by[k].values()), 2),
                "max_drawdown": _metrics.max_drawdown(own.get(k, [])),
                "sd": round(sds[k], 2) if k in sds else None,
                "scale": round(scale[k], 6) if k in scale else None,
            }
        )

    pairs = []
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            w = span([a, b])
            both = [p for p in w if p in by[a] and p in by[b]]
            pairs.append(
                {
                    "a": a,
                    "b": b,
                    "zero_filled": pearson([by[a].get(p, 0.0) for p in w], [by[b].get(p, 0.0) for p in w]),
                    "zero_filled_periods": len(w),
                    "similarity": pearson([by[a][p] for p in both], [by[b][p] for p in both]),
                    "similarity_periods": len(both),
                    "thin": len(both) < min_periods,
                }
            )

    # The combined book over the whole window from the first arm's first close; an arm adds nothing
    # before its own span. Scaled by `weights`.
    window = [p for p in periods if keys and p >= min(first.values())]
    cols = {k: [(by[k].get(p, 0.0) * scale[k]) if p >= first[k] else 0.0 for p in window] for k in keys}
    combined = [sum(cols[k][i] for k in keys) for i in range(len(window))]

    # Effective arms over the window every COUNTED arm spans, where all of them can move. An arm whose
    # span is under min_periods is too new to count: left in, it would shrink that window to its few
    # periods for everyone (contango's first close, a single session, emptied it on 2026-10-17).
    counted = [k for k in keys if len(own[k]) >= min_periods]
    too_new = [k for k in keys if k not in counted]
    shared = span(counted)
    tail = len(window) - len(shared)
    shared_cols = {k: cols[k][tail:] for k in counted}
    shared_sum = [sum(shared_cols[k][i] for k in counted) for i in range(len(shared))]
    sd_sum = _sd(shared_sum)
    effective = (
        round((sum(_sd(shared_cols[k]) for k in counted) / sd_sum) ** 2, 2)
        if sd_sum > 0 and len(counted) > 1
        else None
    )

    combined_dd = _metrics.max_drawdown(combined)
    individual = {k: _metrics.max_drawdown(cols[k]) for k in keys}
    total_individual = round(sum(individual.values()), 2)
    episode = _drawdown_episode(combined)
    in_episode = (
        {k: round(sum(cols[k][episode[0] + 1 : episode[1] + 1]), 2) for k in keys}
        if episode is not None
        else None
    )

    worst = sorted(range(len(window)), key=lambda i: combined[i])[:WORST_PERIODS]
    combined_net = round(sum(combined), 2)
    return {
        "period": period,
        "weights": weights,
        "window": {
            "start": start,
            "end": end,
            "periods": len(periods),
            "from": window[0] if window else None,
            "combined_periods": len(window),
        },  # fmt: skip
        "shared": {
            "from": shared[0] if shared else None,
            "periods": len(shared),
            "thin": len(shared) < min_periods,
            "arms": counted,
            "too_new": too_new,
        },  # fmt: skip
        "min_periods": min_periods,
        "arms": arms,
        "no_closes": [k for k in series if not by[k]],
        "correlation": pairs,
        "effective_arms": effective,
        "drawdown": {
            "combined": combined_dd,
            "sum_of_arms": total_individual,
            "benefit": round(1 - combined_dd / total_individual, 4) if total_individual > 0 else None,
            "by_arm": individual,
            "episode": None
            if episode is None
            else {"from": window[episode[0]] if episode[0] >= 0 else None, "to": window[episode[1]]},
        },
        "worst_periods": [
            {
                "period": window[i],
                "net": round(combined[i], 2),
                "arms_lost": sum(1 for k in keys if cols[k][i] < 0),
                "arms_traded": sum(1 for k in keys if window[i] in by[k]),
                "by_arm": {k: round(cols[k][i], 2) for k in keys if window[i] in by[k]},
            }
            for i in worst
            if combined[i] < 0
        ],
        "contribution": [
            {
                "key": k,
                "net": round(sum(cols[k]), 2),
                "share": round(sum(cols[k]) / combined_net, 4) if combined_net else None,
                "in_worst_drawdown": None if in_episode is None else in_episode[k],
            }
            for k in keys
        ],
        "combined_net": combined_net,
    }
