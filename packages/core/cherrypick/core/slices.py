"""cherrypick.core.slices — an arm's results cut by setup and condition, with guardrails (B5).

`docs/trading-book-review-plan.md` B5. Each cell is one value of one dimension (an environment, an
ATR band, an entry hour, ...) with its n, its mean in R (net in dollars where the schema has no R,
and it says so), and a 95% Student's t interval. A cell under `MIN_N` trades is greyed: shown, never
ranked. The report says how many cells it holds, so one strong cell reads in context.

**In-sample, always.** The report describes the trades it was given. A cell that looks good becomes
a declared hypothesis (B7's register), tested on held-out data with a correction for the number of
cells looked at. It is never turned into a gate from this table.

**The bins are fixed here,** before any table is read, so a cut cannot be moved to flatter a cell.
Pure functions over `core.ledgers` records that already carry B4's context.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from cherrypick.core.stats import mean_sd, t_quantile

MIN_N = 30


def _band(value, edges: Sequence[float], names: Sequence[str]) -> str | None:
    if value is None:
        return None
    for edge, name in zip(edges, names, strict=False):
        if value < edge:
            return name
    return names[-1]


def _hour(r: dict) -> str | None:
    t = str(r.get("entry_time") or "")
    if len(t) < 13 or not t[11:13].isdigit():
        return None
    return f"{t[11:13]}:00"


#: dimension -> how a record is placed in it. Declared once; a new cut is a new line here.
DIMENSIONS: dict[str, Callable[[dict], str | None]] = {
    "setup": lambda r: r.get("strategy") or None,
    "environment": lambda r: r.get("environment"),
    "spx_vs_200d": lambda r: (
        None if r.get("spx_vs_200d") is None else ("above" if r["spx_vs_200d"] >= 0 else "below")
    ),
    "atr_pct": lambda r: _band(r.get("atr_pct"), (1, 2, 4), ("<1%", "1-2%", "2-4%", ">=4%")),
    "rel_volume": lambda r: _band(r.get("rel_volume"), (0.8, 1.2), ("<0.8", "0.8-1.2", ">=1.2")),
    "entry_hour": _hour,
    "holding": lambda r: _band(r.get("holding_days"), (1, 8, 31), ("same day", "1-7d", "8-30d", ">30d")),
}


def cell(values: Sequence[float]) -> dict:
    n = len(values)
    mean, sd = mean_sd(list(values))
    ci = None
    if n >= 2 and sd:
        half = t_quantile(0.975, n - 1) * sd / math.sqrt(n)
        ci = [round(mean - half, 4), round(mean + half, 4)]
    return {"n": n, "mean": None if mean is None else round(mean, 4), "ci95": ci, "greyed": n < MIN_N}


def report(records: Sequence[dict], *, label: str = "") -> dict:
    """The slice report for one arm's records (with B4 context). Unit R where every record has it
    (`r_multiple`), else net dollars."""
    use_r = bool(records) and all(
        r.get("r_multiple") is not None for r in records if r.get("net_pnl") is not None
    )
    key = "r_multiple" if use_r else "net_pnl"
    rows = [r for r in records if r.get(key) is not None]
    dims = {}
    for dim, place in DIMENSIONS.items():
        groups: dict[str, list[float]] = {}
        for r in rows:
            v = place(r)
            if v is not None:
                groups.setdefault(v, []).append(float(r[key]))
        if groups:
            dims[dim] = {v: cell(xs) for v, xs in sorted(groups.items())}
    cells = sum(len(v) for v in dims.values())
    return {
        "label": label,
        "in_sample": True,
        "unit": "R" if use_r else "net $",
        "trades": len(rows),
        "overall": cell([float(r[key]) for r in rows]),
        "cells": cells,
        "ranked_cells": sum(1 for d in dims.values() for c in d.values() if not c["greyed"]),
        "dimensions": dims,
        "note": (
            f"In-sample description of {len(rows)} trades across {cells} cells. A cell under {MIN_N} trades "
            "is greyed, never ranked. A good-looking cell is a hypothesis to declare and test on held-out "
            "data, corrected for the cells looked at; it is never a gate. Trades in one session share a "
            "tape and the interval treats them as independent, so it reads narrower than it is."
        ),
    }
