"""Dolt's revenue consensus for the current and the next fiscal year, landed for the fair-value build
(docs/fair-value-model-plan.md).

The fundamentals landing keeps only the "Next Year" estimates (its score reads one year ahead). The
fair-value forecast needs both years it can see: **year one is the current fiscal year** and year two
the next. Until 2026-10-28 the model took "Next Year" as its year one, a year early on every name.

`sales_consensus` keeps, per weekly snapshot (`date`), each period's consensus with its period end,
the number of analysts and the high and low estimates, so the forecast can judge how far to trust it
(`quality`). Point in time by the snapshot date. Landed by `technicals history fundamentals` after
the statements; read-only against Dolt.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from cherrypick.technicals import fundamentals as _score
from cherrypick.technicals import land as _land

from . import db as _db

SCHEMA = """
CREATE TABLE IF NOT EXISTS sales_consensus (
    symbol TEXT NOT NULL, date TEXT NOT NULL, period TEXT NOT NULL, period_end TEXT,
    consensus REAL, analysts INTEGER, high REAL, low REAL, year_ago REAL,
    PRIMARY KEY (symbol, date, period));
"""
PERIODS = ("Current Year", "Next Year")
#: Fewer analysts than this and the consensus is one or two opinions, not a consensus.
MIN_ANALYSTS = 3
#: A snapshot older than this is not today's view (an analyst update can lag a report a few days).
MAX_AGE_DAYS = 21


def connect(path: Path | None = None):
    return _db.connect(path, SCHEMA)


def land(cfg: dict | None = None, today: date | None = None, path: Path | None = None) -> dict:
    """Copy the current- and next-year sales consensus from the `earnings` clone, from two weeks before
    the newest snapshot held (the whole history, from the fundamentals score's `START`, on the first run)."""
    cfg = {**_land.DEFAULTS, "earnings_db": "earnings", **(cfg or {})}
    today = today or date.today()
    conn = connect(path)
    newest = conn.execute("SELECT MAX(date) FROM sales_consensus").fetchone()[0]
    first = _score.START if newest is None else (date.fromisoformat(newest) - timedelta(days=14)).isoformat()
    started = time.monotonic()
    try:
        dolt = _land._connect(cfg, cfg["earnings_db"])
    except Exception as exc:  # noqa: BLE001 -- no server is a failed landing, reported, not a crash
        conn.close()
        return {"ok": False, "reason": f"dolt unreachable: {type(exc).__name__}: {exc}"}
    f = _land._f
    n = 0
    try:
        cur = dolt.cursor()
        for lo, hi in _land.windows(first, today):
            cur.execute(
                "SELECT act_symbol, date, period, period_end_date, consensus, count, high, low, year_ago "
                "FROM sales_estimate WHERE date >= %s AND date < %s "
                "AND period IN ('Current Year', 'Next Year')",
                (lo, hi),
            )
            rows = [
                (
                    s,
                    d.isoformat(),
                    p,
                    pe.isoformat() if pe else None,
                    f(c),
                    None if k is None else int(k),
                    f(h),
                    f(lw),
                    f(y),
                )
                for s, d, p, pe, c, k, h, lw, y in cur.fetchall()
            ]
            conn.executemany("INSERT OR REPLACE INTO sales_consensus VALUES (?,?,?,?,?,?,?,?,?)", rows)
            conn.commit()
            n += len(rows)
    finally:
        dolt.close()
        conn.close()
    return {"ok": True, "from": first, "rows": n, "seconds": round(time.monotonic() - started)}


@dataclass(frozen=True)
class Year:
    """One fiscal year's revenue consensus as of a snapshot."""

    period_end: str
    revenue: float
    analysts: int | None
    high: float | None
    low: float | None
    snapshot: str


def _has(conn) -> bool:
    """Whether the store has the table (a store landed before 2026-10-28 does not)."""
    sql = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sales_consensus'"
    return conn.execute(sql).fetchone() is not None


def years(conn, symbol: str, as_of: str, after: str) -> list[Year]:
    """The consensus fiscal years ending after `after` (the last reported fiscal year end), from the
    newest snapshot on or before `as_of`, nearest first: the forecast's years one and two. Empty when
    there is no snapshot or it is older than `MAX_AGE_DAYS`."""
    snap = conn.execute(
        "SELECT MAX(date) FROM sales_consensus WHERE symbol = ? AND date <= ?", (symbol, as_of)
    ).fetchone()[0]
    if snap is None or (date.fromisoformat(as_of) - date.fromisoformat(snap)).days > MAX_AGE_DAYS:
        return []
    out = []
    for pe, c, k, h, lw in conn.execute(
        "SELECT period_end, consensus, analysts, high, low FROM sales_consensus "
        "WHERE symbol = ? AND date = ? AND period_end > ? AND consensus > 0 ORDER BY period_end",
        (symbol, snap, after),
    ):
        out.append(Year(pe, c, k, h, lw, snap))
    return out
