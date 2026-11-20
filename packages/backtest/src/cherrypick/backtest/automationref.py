"""An automation's backtest reference: what its pinned Builder run says paper should look like. Pure.

The automation page sets each paper trade and the paper running net against this. One Builder result
(`builder-<name>-<stamp>.json`, the run the operator pins) is reduced to:

- **The usual range of a trade's net**: the 10th, 25th, 50th, 75th and 90th percentiles of the run's
  per-trade net at the verdict kind (the suite's slippage model, as paper's P&L is). The usual range is
  the 10th to 90th, so one trade in five falls outside it by chance.
- **How often it entered**: the share of the run's sessions whose last reason was `entered`, and each
  other reason's share, from the driver's own per-session reasons.
- **How its trades ended**: each exit kind's share of the trades.
- **The band of running-net paths** over the next `horizon` trades: the 10th, 50th and 90th percentile
  of the running net after k resampled trades, k = 1..horizon. The resampling is the suite's stationary
  block bootstrap (`core.stats.stationary_indices`) with the kill criteria's mean block, a week of
  sessions in trades (`core.killcriteria.BLOCK_SESSIONS`), seeded, so the band reproduces.
- **The sample a reading needs, computed**: `core.stats.sessions_needed` with the trades' spread and the
  run's own mean net as the edge, in trades. Used where the register declares no sample; labelled
  computed wherever it is shown.

Under `MIN_TRADES` trades there is no reference: `insufficient`, with the count. Derived from the run's
data, so it goes only to the operator's own console (licensed runs stay licensed); nothing here is a
trial or a verdict, and nothing goes to the advisor.
"""

from __future__ import annotations

import math
import random
from typing import Any

from cherrypick.core import killcriteria, stats

from .automationcards import _net, ordered

#: Fewer trades than this and a percentile or a band would say more than the run does.
MIN_TRADES = 30
PERCENTILES = (0.10, 0.25, 0.50, 0.75, 0.90)
HORIZON = 20
RESAMPLES = 4000
SEED = 20261120


def quantile(sorted_values: list[float], q: float) -> float:
    """The `q` quantile of an ascending list, interpolated between the two nearest ranks."""
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    pos = q * (n - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, n - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _shares(counts: dict[str, int], total: int) -> dict[str, float]:
    return (
        {k: round(v / total, 4) for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))}
        if total
        else {}
    )


def band(
    nets: list[float],
    sessions: int | None,
    *,
    horizon: int = HORIZON,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> list[dict]:
    """The 10th, 50th and 90th percentile of the running net after k = 1..horizon resampled trades."""
    n = len(nets)
    mean_block = killcriteria.BLOCK_SESSIONS * max(1.0, n / sessions) if sessions else 1.0
    rng = random.Random(seed)
    at: list[list[float]] = [[] for _ in range(horizon)]
    for _ in range(resamples):
        total = 0.0
        for k, i in enumerate(stats.stationary_indices(n, mean_block, rng, length=horizon)):
            total += nets[i]
            at[k].append(total)
    out = []
    for k, values in enumerate(at, start=1):
        values.sort()
        out.append(
            {
                "k": k,
                "p10": round(quantile(values, 0.10), 2),
                "p50": round(quantile(values, 0.50), 2),
                "p90": round(quantile(values, 0.90), 2),
            }
        )
    return out


def reference(
    doc: dict, name: str, *, horizon: int = HORIZON, resamples: int = RESAMPLES, seed: int = SEED
) -> dict:
    """Automation `name`'s reference from Builder result `doc`."""
    trades = ordered([t for t in doc.get("trades") or [] if t.get("arm", name) == name])
    summary = (doc.get("summary") or {}).get(name) or {}
    reasons = {str(k): int(v) for k, v in (summary.get("reasons") or {}).items()}
    sessions = summary.get("sessions")
    if not isinstance(sessions, int):
        sessions = sum(reasons.values()) or doc.get("session_count")
    head: dict[str, Any] = {
        "run": doc.get("name"),
        "automation": name,
        "symbol": doc.get("symbol"),
        "source": doc.get("source"),
        "first": (doc.get("sessions") or [None])[0] if isinstance(doc.get("sessions"), list) else None,
        "last": (doc.get("sessions") or [None])[-1] if isinstance(doc.get("sessions"), list) else None,
        "generated_at": doc.get("generated_at"),
        "trades": len(trades),
        "sessions": sessions,
    }
    if len(trades) < MIN_TRADES:
        return {**head, "insufficient": True, "min_trades": MIN_TRADES}
    nets = [_net(t) for t in trades]
    ranked = sorted(nets)
    mean = sum(nets) / len(nets)
    sd = math.sqrt(sum((x - mean) ** 2 for x in nets) / (len(nets) - 1))
    entered = reasons.get("entered", 0)
    exits: dict[str, int] = {}
    for t in trades:
        k = str(t.get("exit_kind") or "unknown")
        exits[k] = exits.get(k, 0) + 1
    return {
        **head,
        "insufficient": False,
        "mean": round(mean, 2),
        "sd": round(sd, 2),
        "percentiles": {f"p{round(q * 100)}": round(quantile(ranked, q), 2) for q in PERCENTILES},
        "entry_rate": round(entered / sessions, 4) if sessions else None,
        "missed": _shares({k: v for k, v in reasons.items() if k != "entered"}, sessions or 0),
        "exits": _shares(exits, len(trades)),
        "band": band(nets, sessions, horizon=horizon, resamples=resamples, seed=seed),
        "band_resamples": resamples,
        "sample_computed": stats.sessions_needed(sd, mean) if mean > 0 else None,
    }
