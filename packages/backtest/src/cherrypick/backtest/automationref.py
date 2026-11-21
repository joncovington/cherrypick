"""An automation's backtest reference: what its pinned Builder run says paper should look like. Pure.

The automation page sets each paper trade and the paper running net against this. One Builder result
(`builder-<name>-<stamp>.json`, the run the operator pins) is reduced to:

- **The usual range of a trade's net**: the 10th, 25th, 50th, 75th and 90th percentiles of the run's
  per-trade net at the verdict kind (the suite's slippage model, as paper's P&L is). The usual range is
  the 10th to 90th, so one trade in five falls outside it by chance.
- **How often it entered**: each last reason's count (`reasons`, short runs included), the share of
  the run's sessions whose last reason was `entered`, and each other reason's share, from the driver's
  own per-session reasons.
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
        "reasons": reasons,
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


# --------------------------------------------------------------------------- paper against the reference
#: Fewer paper trades than this and the page says "too few" rather than a place.
MIN_PAPER = 5
#: The band is drawn this many trades past the paper record, so the page shows the road ahead.
AHEAD = 20


def place(value: float, low: float, high: float) -> str:
    """`below`, `inside` or `above` the closed range [low, high]."""
    return "below" if value < low else "above" if value > high else "inside"


def percentile_of(sorted_values: list[float], x: float) -> float:
    """Where `x` falls among the run's trade nets, 0 to 100: the share at or below it (ties half)."""
    below = sum(1 for v in sorted_values if v < x)
    equal = sum(1 for v in sorted_values if v == x)
    return round(100 * (below + equal / 2) / len(sorted_values), 1)


def reading(
    doc: dict,
    name: str,
    paper: list[dict],
    entries: dict[str, int] | None = None,
    *,
    declared: int | None = None,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> dict:
    """Automation `name`'s paper record (`paper`: its closed trades in close order, each with `session` and
    `net`) set against its pinned Builder run `doc`.

    - Each trade's place against the run's usual range (10th to 90th percentile of a trade's net) and its
      percentile among the run's trades.
    - The running net, with the run's band of paths drawn to `AHEAD` trades past the record, and where the
      running net now sits in the band at its own trade count.
    - The sample: `declared` (the automation's forward rule's `min_obs`) when given, else the run's
      computed one, with how many trades paper has.
    - Paper's entry rate from the session register (`entries`: entered and missed arm-days; pending ones
      are not counted), beside the run's.
    - `state`: `no_reference` (the run has too few trades), `no_trades`, `too_few` (under `MIN_PAPER`),
      else the running net's place: `inside`, `below` or `above` the band.
    """
    n = len(paper)
    ref = reference(doc, name, horizon=max(HORIZON, n + AHEAD), resamples=resamples, seed=seed)
    entered = (entries or {}).get("entered", 0)
    missed = (entries or {}).get("missed", 0)
    paper_entry = {
        "entered": entered,
        "missed": missed,
        "rate": round(entered / (entered + missed), 4) if entered + missed else None,
    }
    if ref["insufficient"]:
        return {"reference": ref, "state": "no_reference", "paper": {"trades": n, "entry": paper_entry}}
    p = ref["percentiles"]
    ranked = sorted(_net(t) for t in doc.get("trades") or [] if t.get("arm", name) == name)
    rows, total = [], 0.0
    for i, t in enumerate(paper, start=1):
        net = float(t.get("net") or 0.0)
        total += net
        rows.append(
            {
                "i": i,
                "session": t.get("session"),
                "net": round(net, 2),
                "place": place(net, p["p10"], p["p90"]),
                "percentile": percentile_of(ranked, net),
                "running": round(total, 2),
            }
        )
    counts = {k: sum(1 for r in rows if r["place"] == k) for k in ("below", "inside", "above")}
    at = ref["band"][n - 1] if n else None
    running_place = place(total, at["p10"], at["p90"]) if at else None
    state = "no_trades" if n == 0 else "too_few" if n < MIN_PAPER else running_place
    needed = declared if isinstance(declared, int) and declared > 0 else ref["sample_computed"]
    return {
        "reference": ref,
        "state": state,
        "paper": {
            "trades": n,
            "rows": rows,
            "counts": counts,
            "net": round(total, 2),
            "mean": round(total / n, 2) if n else None,
            "running_place": running_place,
            "band_at": at,
            "entry": paper_entry,
        },
        "sample": {
            "needed": needed,
            "basis": "declared"
            if needed is not None and needed == declared
            else "computed"
            if needed is not None
            else None,
            "have": n,
        },
    }


def paper_record(db_path: str, name: str) -> tuple[list[dict], dict[str, int]]:
    """Automation `name`'s closed paper trades in close order (core's ledger reader, read-only) and its
    session register's entered and missed arm-days. Empty when the ledger does not exist yet."""
    import sqlite3
    from pathlib import Path

    from cherrypick.core import ledgers as core_ledgers

    if not Path(db_path).exists():
        return [], {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [r for r in core_ledgers.READERS["ensemble_trade"](conn) if r.get("arm") == name]
        rows.sort(key=lambda r: (str(r.get("session") or ""), str(r.get("exit_time") or "")))
        paper = [{"session": r.get("session"), "net": r.get("net_pnl")} for r in rows]
        try:
            counts = dict(
                conn.execute(
                    "SELECT outcome, COUNT(*) FROM ensemble_entries WHERE arm = ? GROUP BY outcome", (name,)
                ).fetchall()
            )
        except sqlite3.OperationalError:
            counts = {}
    finally:
        conn.close()
    return paper, {k: int(v) for k, v in counts.items()}


def paper_db_path() -> str:
    """The automations paper ledger: its override, else `data/automations/paper_trades.db` (the module's own
    rule, spelt here because this package imports no strategy module)."""
    import os

    from cherrypick.core import home

    return (
        os.environ.get("AUTOMATIONS_DB_PATH")
        or os.environ.get("ENSEMBLE_DB_PATH")
        or str(home.data_dir("automations") / "paper_trades.db")
    )
