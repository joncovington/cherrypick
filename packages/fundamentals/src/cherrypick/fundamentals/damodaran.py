"""Aswath Damodaran's public valuation inputs, read from the files scripts/fetch_damodaran.py writes.

- `erp(as_of)`: the implied equity risk premium of the newest month starting on or before the
  session, with that month, so a backtest reads the premium it would have had.
- `rating(coverage, financial)`: the synthetic rating and default spread for an interest coverage
  ratio (operating profit over interest expense), from the coverage-to-rating table for large
  non-financial firms or for financial firms. The table is as of one date (its `as_of`); a history
  run reads today's spreads for every year, which the run states.

Network-free; a missing file reads as None so the model can fall back and say so.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import paths


def _load(name: str, root: Path | None = None) -> dict | None:
    try:
        return json.loads(((root or paths.damodaran_dir()) / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def erp(as_of: str, root: Path | None = None) -> tuple[float, str] | None:
    """(the implied ERP, the month it is for) for the newest month on or before `as_of`."""
    doc = _load("erp.json", root)
    if not doc:
        return None
    months = sorted((m, v) for m, v in (doc.get("months") or {}).items() if m <= as_of)
    return (float(months[-1][1]), months[-1][0]) if months else None


def rating(
    coverage: float, financial: bool = False, root: Path | None = None
) -> tuple[str, float, str] | None:
    """(the synthetic rating, its default spread, the table's date) for an interest coverage ratio."""
    doc = _load("ratings.json", root)
    if not doc:
        return None
    for lo, hi, name, spread in doc.get("financial" if financial else "non_financial") or []:
        if lo <= coverage <= hi or (coverage > hi and hi >= 100000):
            return name, float(spread), str(doc.get("as_of") or "")
    bands = doc.get("financial" if financial else "non_financial") or []
    if bands and coverage < bands[0][0]:
        return bands[0][2], float(bands[0][3]), str(doc.get("as_of") or "")
    # Between two bands' rounded edges (4.2499999 < x < 4.25): the next band up.
    for lo, _hi, name, spread in bands:
        if coverage < lo:
            return name, float(spread), str(doc.get("as_of") or "")
    return None
