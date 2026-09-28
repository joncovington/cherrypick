"""The end-of-day store: Dolt's raw bars, splits, dividends and implied-volatility history, kept as
Dolt last stated them, plus a log of every landing.

Raw, not adjusted: the adjusted series is `adjust.adjust` over these rows, computed on read. Upserts
replace a row wholesale, because Dolt's clones are corrected upstream from time to time and the
store's job is to hold what the source now says, not what it said first.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from . import adjust as _adjust
from . import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (symbol, date));
CREATE TABLE IF NOT EXISTS splits (
    symbol TEXT NOT NULL, ex_date TEXT NOT NULL, to_factor REAL NOT NULL, for_factor REAL NOT NULL,
    PRIMARY KEY (symbol, ex_date));
CREATE TABLE IF NOT EXISTS dividends (
    symbol TEXT NOT NULL, ex_date TEXT NOT NULL, amount REAL NOT NULL,
    PRIMARY KEY (symbol, ex_date));
CREATE TABLE IF NOT EXISTS iv (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    iv REAL, iv_year_high REAL, iv_year_low REAL, hv REAL,
    PRIMARY KEY (symbol, date));
CREATE TABLE IF NOT EXISTS listings (
    symbol TEXT PRIMARY KEY, is_etf INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS landings (
    landed_at REAL NOT NULL, through TEXT, symbols INTEGER, bars INTEGER, report TEXT);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or paths.eod_db()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_bars(conn, rows) -> int:
    rows = list(rows)
    conn.executemany("INSERT OR REPLACE INTO bars VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def upsert_splits(conn, rows) -> int:
    rows = list(rows)
    conn.executemany("INSERT OR REPLACE INTO splits VALUES (?, ?, ?, ?)", rows)
    return len(rows)


def upsert_dividends(conn, rows) -> int:
    rows = list(rows)
    conn.executemany("INSERT OR REPLACE INTO dividends VALUES (?, ?, ?)", rows)
    return len(rows)


def upsert_iv(conn, rows) -> int:
    rows = list(rows)
    conn.executemany("INSERT OR REPLACE INTO iv VALUES (?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def upsert_listings(conn, rows) -> int:
    rows = list(rows)
    conn.executemany("INSERT OR REPLACE INTO listings VALUES (?, ?)", rows)
    return len(rows)


def stocks(conn, symbols: list[str]) -> list[str]:
    """The given symbols Dolt lists as operating companies, not funds. Breadth is a stock measure:
    the vendor's table holds no ETFs, and counting the rotation funds in it would move the share."""
    etf = {r["symbol"] for r in conn.execute("SELECT symbol FROM listings WHERE is_etf = 1")}
    return [s for s in symbols if s not in etf]


def record_landing(conn, through: str | None, symbols: int, bars: int, report: dict) -> None:
    conn.execute(
        "INSERT INTO landings VALUES (?, ?, ?, ?, ?)",
        (time.time(), through, symbols, bars, json.dumps(report)),
    )


def latest_dates(conn) -> dict[str, str]:
    return {
        r["symbol"]: r["d"] for r in conn.execute("SELECT symbol, MAX(date) AS d FROM bars GROUP BY symbol")
    }


def latest_iv_dates(conn) -> dict[str, str]:
    return {
        r["symbol"]: r["d"] for r in conn.execute("SELECT symbol, MAX(date) AS d FROM iv GROUP BY symbol")
    }


def raw_bars(conn, symbol: str) -> list[_adjust.Bar]:
    return [
        _adjust.Bar(r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"] or 0.0)
        for r in conn.execute("SELECT * FROM bars WHERE symbol = ? ORDER BY date", (symbol,))
    ]


_TASTY: dict = {"mtime": None, "symbols": {}}


def tastytrade_dividends(symbol: str) -> list[_adjust.Dividend]:
    """Tastytrade's history for `symbol` from scripts/fetch_dividends.py's file, re-read only when
    the file changes. Empty when the file or the symbol is absent: Dolt alone then decides."""
    path = paths.tastytrade_dividends()
    try:
        stamp = (str(path), path.stat().st_mtime)  # the path too: two files can share a timestamp
        if stamp != _TASTY["mtime"]:
            _TASTY.update(
                mtime=stamp, symbols=json.loads(path.read_text(encoding="utf-8")).get("symbols") or {}
            )
    except (OSError, ValueError):
        return []
    rows = (_TASTY["symbols"].get(symbol) or {}).get("dividends") or []
    return [_adjust.Dividend(d, float(a)) for d, a in rows]


def dividends(conn, symbol: str) -> tuple[list[_adjust.Dividend], list[dict]]:
    """The dividends adjusted bars use: Dolt's reconciled with tastytrade's (`dividends.reconcile`)."""
    from .dividends import reconcile

    dolt = [
        _adjust.Dividend(r["ex_date"], r["amount"])
        for r in conn.execute("SELECT * FROM dividends WHERE symbol = ?", (symbol,))
    ]
    return reconcile(dolt, tastytrade_dividends(symbol))


def adjusted_bars(conn, symbol: str) -> list[_adjust.AdjustedBar]:
    splits = [
        _adjust.Split(r["ex_date"], r["to_factor"], r["for_factor"])
        for r in conn.execute("SELECT * FROM splits WHERE symbol = ?", (symbol,))
    ]
    raw = raw_bars(conn, symbol)
    adjusted = _adjust.adjust(raw, _adjust.dedupe_splits(raw, splits), dividends(conn, symbol)[0])
    return adjusted[_adjust.series_break(adjusted) :]


def iv_rank(conn, symbol: str, on: str | None = None) -> dict | None:
    """Where the latest IV (on or before `on`) sits between its one-year low and high, 0-100, as
    Dolt's volatility_history states them. None when there is no row or the range is empty."""
    q = "SELECT * FROM iv WHERE symbol = ? AND iv IS NOT NULL"
    args: list = [symbol]
    if on:
        q += " AND date <= ?"
        args.append(on)
    row = conn.execute(q + " ORDER BY date DESC LIMIT 1", args).fetchone()
    if row is None or row["iv_year_high"] is None or row["iv_year_low"] is None:
        return None
    span = row["iv_year_high"] - row["iv_year_low"]
    if span <= 0:
        return None
    return {
        "date": row["date"],
        "iv": row["iv"],
        "iv_rank": round(100.0 * (row["iv"] - row["iv_year_low"]) / span, 1),
    }
