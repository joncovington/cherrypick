"""Treasury's daily par yield curve, as the suite's one risk-free read.

`scripts/fetch_market_files.py` writes one CSV a calendar year to `market-files/treasury/<year>.csv`
(Treasury posts the curve by about 18:00 ET). Two readers want it: the overview pack's yields block
(`packages/overview`, since 2026-09) and the fair-value build's risk-free rate (`packages/technicals`,
2026-10-28). Hoisted here from `overview.files` so one parser validates the file on arrival and
reads it after, and so technicals need not import overview.

Every read answers "strictly before the session": a curve dated on the session itself is a close
that has not happened when the morning jobs run. A missing, stale or unparseable file yields an
empty list or None, never a guess; the caller says what it fell back to.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from pathlib import Path

from cherrypick.core import home as _home

# The tenors the suite keeps, by Treasury's own column names.
TENORS = {"3m": "3 Mo", "2y": "2 Yr", "5y": "5 Yr", "10y": "10 Yr", "30y": "30 Yr"}


def path(year: int) -> Path:
    return _home.data_dir("market-files") / "treasury" / f"{year}.csv"


def _us_date(text: str) -> date | None:
    try:
        return datetime.strptime(text.strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def parse(text: str) -> list[tuple[date, dict[str, float]]]:
    """[(session, {tenor: yield %})] oldest first, for the tenors in `TENORS` that the row carries."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames or "Date" not in reader.fieldnames:
        return []
    out = []
    for row in reader:
        day = _us_date(row.get("Date") or "")
        if not day:
            continue
        curve = {}
        for key, column in TENORS.items():
            try:
                curve[key] = float(row.get(column) or "")
            except ValueError:
                continue
        if curve:
            out.append((day, curve))
    out.sort()
    return out


def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def curves_before(session: str) -> list[tuple[date, dict[str, float]]]:
    """Treasury's curves strictly before `session`, oldest first, across the session's year and the
    one before (the first sessions of January need December)."""
    day = date.fromisoformat(session)
    rows = []
    for year in (day.year - 1, day.year):
        text = _read(path(year))
        if text:
            rows.extend(parse(text))
    return sorted(r for r in rows if r[0] < day)


def yield_before(session: str, tenor: str = "10y") -> tuple[date, float] | None:
    """(curve date, yield %) of the last curve strictly before `session` that carries `tenor`."""
    for day, curve in reversed(curves_before(session)):
        if tenor in curve:
            return day, curve[tenor]
    return None
