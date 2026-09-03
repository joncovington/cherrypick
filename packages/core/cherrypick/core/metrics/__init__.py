"""cherrypick.core.metrics — the shared calibration metric bundle.

One metric vocabulary for promotion evidence, computed over the suite's NORMALIZED
closed-trade records (the shape the orchestrator's per-schema readers emit: dicts with
`net_pnl`, and optionally `capital`, `session`, `slippage`). The three modules keep their
own richer per-module analytics; THIS bundle is what `calibrate` injects into
`compare_profiles`, so every rung on every module's ladder is judged in the same units:

- return_on_capital: a 2-wide and a 10-wide IC must not weigh equally — net P&L as a
  fraction of the capital genuinely at risk.
- sharpe: per-trade, deliberately NOT annualized (discrete event trades; annualizing a
  0DTE series and an overnight-earnings series differently would defeat comparability).
- max_drawdown: over the session-ordered equity path of the group.
- Unknowns stay None, never 0: a record without capital contributes nothing to RoC, and
  the *_coverage counts say how much of the sample carries each datum. A misleadingly
  precise 0.00 is worse than an honest dash (the MEIC dashboard's _risk_metrics rule).

Pure functions; no I/O, no config.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence


def return_on_capital(records: Sequence[Mapping]) -> float | None:
    """Sum(net_pnl) / sum(capital) over the records that carry a positive capital.
    None when no record does — unknown capital is not free capital."""
    capitals = [r["capital"] for r in records if r.get("capital")]
    if not capitals:
        return None
    nets = [r["net_pnl"] for r in records if r.get("capital")]
    total_capital = sum(capitals)
    return round(sum(nets) / total_capital, 4) if total_capital > 0 else None


def capture_rate(records: Sequence[Mapping]) -> dict:
    """{"value", "n"}: sum(net_pnl) / sum(max_profit) over the records that carry a positive
    max_profit -- how much of the structures' own theoretical ceiling the group actually kept.
    `value` is None when no record carries max_profit (a schema that never populates it, per
    `ledgers`' own None-for-debit/geometry-dependent rule); `n` is the coverage count either way,
    same shape as `return_on_capital`'s own "unknown is not free/zero" discipline."""
    profits = [r["max_profit"] for r in records if r.get("max_profit")]
    n = len(profits)
    if n == 0:
        return {"value": None, "n": 0}
    nets = [r["net_pnl"] for r in records if r.get("max_profit")]
    total = sum(profits)
    return {"value": round(sum(nets) / total, 4) if total > 0 else None, "n": n}


def max_profit_pct(records: Sequence[Mapping]) -> dict:
    """{"median", "n"}: median of net_pnl / max_profit over WINNING trades (net_pnl > 0) that
    carry max_profit -- how close a typical win came to the structure's own ceiling. A losing
    trade is excluded on purpose: a negative ratio here would be a fact about capture_rate's
    aggregate, not about how well a WIN was captured, and pooling the two would blur both."""
    ratios = [r["net_pnl"] / r["max_profit"] for r in records if r.get("max_profit") and r["net_pnl"] > 0]
    return {"median": round(statistics.median(ratios), 4) if ratios else None, "n": len(ratios)}


def max_loss_pct(records: Sequence[Mapping]) -> dict:
    """{"median", "n"}: median of |net_pnl| / capital over LOSING trades (net_pnl < 0) that carry
    capital -- how close a typical loss came to the structure's own defined max loss. Mirrors
    `max_profit_pct`'s split by sign, over `capital` (every schema's defined-risk bound) rather
    than `max_profit` (only ever populated for a plain credit spread) since a loss is bounded by
    capital at risk regardless of whether the structure's profit ceiling is known."""
    ratios = [abs(r["net_pnl"]) / r["capital"] for r in records if r.get("capital") and r["net_pnl"] < 0]
    return {"median": round(statistics.median(ratios), 4) if ratios else None, "n": len(ratios)}


def sharpe(values: Sequence[float]) -> float | None:
    """Per-trade Sharpe (mean/stdev of the per-trade net P&L series), un-annualized.
    None below 2 samples or on a zero-variance series — not a fabricated 0."""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    if var <= 0:
        return None
    return round(mean / var**0.5, 3)


def max_drawdown(values: Sequence[float]) -> float:
    """Largest peak-to-trough fall of the running-sum equity path of `values` (in order).
    0.0 for an empty or never-drawn-down series; always >= 0."""
    running = peak = dd = 0.0
    for v in values:
        running += v
        peak = max(peak, running)
        dd = max(dd, peak - running)
    return round(dd, 2)


def expectancy(values: Sequence[float]) -> float | None:
    """Mean per-trade net — the edge in dollars, the number a sample gate multiplies.
    None on an empty series, never a fabricated 0."""
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def profit_factor(values: Sequence[float]) -> float | None:
    """Gross profits over gross losses. None when either side is empty: a book with no losses
    yet has an UNDEFINED profit factor, not an infinite one, and a book with no wins is not a
    zero-quality reading but an unmeasurable ratio."""
    gains = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    if gains <= 0 or losses <= 0:
        return None
    return round(gains / losses, 3)


def sortino(values: Sequence[float]) -> float | None:
    """Per-trade Sortino: mean over the downside deviation (target 0, deviation over the whole
    sample, standard form), un-annualized for the same comparability reason as `sharpe`.
    None below 2 losing samples — one loss says nothing about the SHAPE of the downside, and a
    lossless series has an undefined ratio, not an infinite one."""
    n = len(values)
    losses = [v for v in values if v < 0]
    if n < 2 or len(losses) < 2:
        return None
    downside_var = sum(min(v, 0.0) ** 2 for v in values) / n
    if downside_var <= 0:
        return None
    return round((sum(values) / n) / downside_var**0.5, 3)


def sqn(values: Sequence[float]) -> float | None:
    """System Quality Number (Van Tharp): sqrt(n) x mean / stdev of the per-trade nets — edge,
    consistency and sample size in one score. Reported BESIDE `sample_progress`, never replacing
    it: SQN folds the sample into the number, the progress gate keeps it visible. None below 2
    samples or on a zero-variance series."""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    if var <= 0:
        return None
    return round((n**0.5) * mean / var**0.5, 2)


# The tail metrics key on SESSIONS, not trades — rows are not draws (the flies regime_coverage
# lesson: twenty trades on one day observe one market between them). Stamped at implementation
# per the plan: CVaR is the mean of the worst 10% of per-session nets, refused below 20 sessions —
# a CVaR over six sessions reads as a risk number and is not one.
CVAR_QUANTILE = 0.10
CVAR_MIN_SESSIONS = 20


def session_nets(records: Sequence[Mapping]) -> list[float]:
    """Per-session net P&L in session order — the series every tail metric keys on. Records
    without a session are excluded (they cannot be pooled into a day that is not known)."""
    by_session: dict[str, float] = {}
    for r in records:
        s = r.get("session")
        if s:
            by_session[s] = by_session.get(s, 0.0) + r["net_pnl"]
    return [round(by_session[s], 2) for s in sorted(by_session)]


def session_nets_dated(records: Sequence[Mapping]) -> list[tuple[str, float]]:
    """`session_nets`, paired with the session it belongs to -- for a caller that needs the date
    label (a chart x-axis), not just the ordered value series. Same pooling and exclusion rule,
    so the two never disagree about which sessions exist or their order; kept as a second
    function rather than a flag on `session_nets` so every existing tail-metric call site
    (whose contract is a plain float series) is untouched."""
    by_session: dict[str, float] = {}
    for r in records:
        s = r.get("session")
        if s:
            by_session[s] = by_session.get(s, 0.0) + r["net_pnl"]
    return [(s, round(by_session[s], 2)) for s in sorted(by_session)]


def worst_session(records: Sequence[Mapping]) -> dict | None:
    """The single worst session: {"session", "net"}. None when no record carries a session."""
    by_session: dict[str, float] = {}
    for r in records:
        s = r.get("session")
        if s:
            by_session[s] = by_session.get(s, 0.0) + r["net_pnl"]
    if not by_session:
        return None
    s = min(by_session, key=lambda k: by_session[k])
    return {"session": s, "net": round(by_session[s], 2)}


def cvar(
    values: Sequence[float], quantile: float = CVAR_QUANTILE, min_n: int = CVAR_MIN_SESSIONS
) -> float | None:
    """Expected shortfall over per-session nets: the mean of the worst `quantile` of sessions
    (at least one). Refuses (None) below `min_n` sessions — see the constants above."""
    n = len(values)
    if n < min_n:
        return None
    k = max(1, int(n * quantile))
    worst = sorted(values)[:k]
    return round(sum(worst) / k, 2)


def drawdown_span(values: Sequence[float]) -> dict:
    """Duration to `max_drawdown`'s depth: the longest peak-to-recovery stretch of the running-sum
    path, in observations (sessions when fed `session_nets`). {"longest": int, "open": int} —
    `open` is the stretch still below its peak at the end of the series, NOT clamped into
    `longest`: an ongoing drawdown is a different fact from a survived one, and folding them
    together would report a live bleed as history."""
    running = peak = 0.0
    longest = current = 0
    for v in values:
        running += v
        if running >= peak:
            peak = running
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return {"longest": longest, "open": current}


def sample_progress(n: int, targets: Sequence[int] = (30, 100)) -> dict:
    """Progress toward the significance targets (earnings' 30/100 convention, suite-wide):
    {"n", "targets", "next_target", "progress"} — progress is n over the next unmet target,
    capped at 1.0 when every target is met."""
    targets = sorted(targets)
    nxt = next((t for t in targets if n < t), None)
    return {
        "n": n,
        "targets": list(targets),
        "next_target": nxt,
        "progress": round(min(n / nxt, 1.0), 4) if nxt else 1.0,
    }


def calibration_reading(records: Sequence[Mapping]) -> dict:
    """The promotion-evidence bundle for one attribution group of normalized records.

    Extends the original sample/win_rate/days/net_pnl reading with the comparability
    metrics (return_on_capital, sharpe, max_drawdown over the session-ordered path),
    the cost-sensitivity restatement (net at a doubled slippage fraction — linear, so
    it is net minus the recorded slippage), and the coverage counts that keep partial
    instrumentation honest. Shapes match what `recommend_promotion` reads.

    `net_pnl_2x_slippage` and `return_on_capital` render None rather than a value quietly
    computed from an incomplete or unmeasured sample (found live 2026-08-14: several arms
    reported net_pnl_2x_slippage identical to net_pnl because slippage was never recorded on
    any of their trades — sum-of-nothing summed to 0, indistinguishable from a genuinely
    zero-slippage arm unless the coverage is checked). `net_pnl_2x_slippage` needs the WHOLE
    sample's slippage known, same requirement `_qualify_one`'s `require_slippage_survival`
    check already enforces one level up — this makes the number itself honest rather than
    leaving an honest reading downstream of a dishonest one. A record's slippage summing to
    exactly 0 even at full coverage is treated the same way: a real per-trade slippage model
    essentially never nets to precisely zero over more than a couple of trades, so that pattern
    reads as "never wired up" rather than "measured and happened to be zero." Both checks are
    skipped for an empty group (n=0), where a literal 0.0 is not standing in for anything
    unmeasured."""
    ordered = sorted(records, key=lambda r: r.get("session") or "")
    nets = [r["net_pnl"] for r in ordered]
    n = len(nets)
    wins = sum(1 for v in nets if v > 0)
    sessions = {r.get("session") for r in ordered if r.get("session")}
    known_slips = [r["slippage"] for r in ordered if r.get("slippage") is not None]
    slippage_coverage = len(known_slips)
    stressed = round(sum(nets) - sum(known_slips), 2)
    if n > 0 and (slippage_coverage < n or sum(known_slips) == 0):
        stressed = None
    capital_coverage = sum(1 for r in ordered if r.get("capital"))
    roc = return_on_capital(ordered) if capital_coverage == n and n > 0 else None
    max_profit_coverage = sum(1 for r in ordered if r.get("max_profit"))
    return {
        "sample": n,
        "win_rate": round(wins / n, 4) if n else None,
        "days": len(sessions),
        "net_pnl": round(sum(nets), 2),
        "net_pnl_2x_slippage": stressed,
        "slippage_coverage": slippage_coverage,
        "return_on_capital": roc,
        "capital_coverage": capital_coverage,
        "sharpe": sharpe(nets),
        "max_drawdown": max_drawdown(nets),
        "sample_progress": sample_progress(n),
        # 2026-08-23 expansion (docs/metrics-plan.md phase 1): edge and risk-adjusted per-trade,
        # tail and duration per-session. Report-only — no qualification rule reads these yet; the
        # promotion-gate question is deliberately deferred (see the plan's open questions).
        "expectancy": expectancy(nets),
        "profit_factor": profit_factor(nets),
        "sortino": sortino(nets),
        "sqn": sqn(nets),
        "worst_session": worst_session(ordered),
        "cvar": cvar(session_nets(ordered)),
        "cvar_quantile": CVAR_QUANTILE,
        "cvar_min_sessions": CVAR_MIN_SESSIONS,
        "drawdown_span": drawdown_span(session_nets(ordered)),
        # 2026-09 expansion (docs/metrics-plan.md's own capture-rate idea, console Phase 3a):
        # capture_rate needs FULL max_profit coverage across the sample before reporting a value,
        # same discipline as return_on_capital above -- a partial-coverage ratio would silently
        # compare a smaller, unstated sub-sample against the group's headline `n`. max_profit_pct/
        # max_loss_pct are per-trade distributions (median), so they report over whatever coverage
        # exists rather than requiring the whole sample -- a median is legitimately meaningful over
        # a subset, unlike a summed ratio.
        "capture_rate": (
            capture_rate(ordered)
            if max_profit_coverage == n and n > 0
            else {"value": None, "n": max_profit_coverage}
        ),
        "max_profit_coverage": max_profit_coverage,
        "max_profit_pct": max_profit_pct(ordered),
        "max_loss_pct": max_loss_pct(ordered),
    }
