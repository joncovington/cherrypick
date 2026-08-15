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
    }
