"""Fetch a symbol's split history from a public source, once, into the `split_history` table.

**Why.** Dolt's split table misses splits: on 2026-10-01 it held three of TQQQ's eight (2021, 2022,
2025), so every bar before 2018-05-24 adjusted wrongly -- five fake crashes, one of them -67%.
tastytrade has no split endpoint (its corporate-events API serves dividends and earnings only), so
the second source is a public split-history page. This is a one-off per symbol, run by hand when a
symbol turns out to need it, never scheduled: split histories change only when a split happens.

**Checked against the prices, not trusted.** Each fetched split is compared with the local Dolt
clone's raw prices: the prior session's close over the ex-date's open should equal the split's
ratio. Rows whose prices agree within `TOLERANCE` are `verified = 1`, rows they contradict are
`verified = 0` (kept, so the disagreement is on record, but technicals will not apply them), and
rows the clone cannot check (no bars there, or no `dolt`) are `verified = NULL`.

A script, not package code, because it reaches the network; `packages/technicals` stays
network-free and reads this table read-only (`store.public_splits`). A refetch replaces that
symbol's rows wholesale.

    python scripts/fetch_split_history.py TQQQ [SYMBOL ...] [--dry-run]
    python scripts/fetch_split_history.py SVXY --declare 2018-09-18:1:4 [...] --source "<citation>"

**Declared splits** (`--declare DATE:TO:FOR`, 2026-10-05) are for a symbol the public page does not
carry (SVXY: 404). They need a `--source` citing where the split is recorded, and go through the SAME
raw-price check as a fetched row: a declaration the prices contradict is stored `verified = 0` and
never applied. All of a symbol's declared splits are given in one run, since a run replaces that
symbol's rows wholesale.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from cherrypick.core import home as _home
from cherrypick.technicals import paths

SOURCE = "https://www.splithistory.com/{symbol}/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) cherrypick-split-history (one-time fetch)"
PAUSE_S = 2.0  # between symbols: a person's pace, not a crawler's
TOLERANCE = 0.10  # relative gap between the raw overnight ratio and the split's ratio

# <TD ...>05/24/2018</TD><TD ...>3 for 1</TD>
ROW = re.compile(
    r"<td[^>]*>\s*(\d{2})/(\d{2})/(\d{4})\s*</td>\s*<td[^>]*>\s*([\d.]+)\s*for\s*([\d.]+)\s*</td>",
    re.IGNORECASE,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS split_history (
    symbol TEXT NOT NULL, ex_date TEXT NOT NULL,
    to_factor REAL NOT NULL, for_factor REAL NOT NULL,   -- `to` shares after for every `for` before
    source TEXT NOT NULL, fetched_at TEXT NOT NULL,
    raw_ratio REAL,      -- prior raw close / ex-date raw open, from the local Dolt clone
    verified INTEGER,    -- 1 the prices agree, 0 they contradict, NULL could not check
    PRIMARY KEY (symbol, ex_date));
"""


def parse(html: str) -> list[tuple[str, float, float]]:
    """(ex_date, to_factor, for_factor) rows from the page's split table, oldest first."""
    rows = {
        f"{y}-{m}-{d}": (float(to), float(fr))
        for m, d, y, to, fr in ROW.findall(html)
        if float(to) > 0 and float(fr) > 0
    }
    return [(d, to, fr) for d, (to, fr) in sorted(rows.items())]


def declared(specs: list[str]) -> list[tuple[str, float, float]]:
    """`DATE:TO:FOR` -> (ex_date, to_factor, for_factor); a malformed spec raises."""
    out = []
    for spec in specs:
        ex, to, fr = spec.split(":")
        date.fromisoformat(ex)
        if float(to) <= 0 or float(fr) <= 0:
            raise ValueError(f"split factors must be positive: {spec}")
        out.append((ex, float(to), float(fr)))
    return out


def fetch(symbol: str) -> str:
    req = urllib.request.Request(SOURCE.format(symbol=symbol.lower()), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _dolt_stocks() -> Path:
    return _home.data_dir("earnings") / "stocks"


def raw_ratio(symbol: str, ex_date: str) -> float | None:
    """Last raw close before the ex-date / first raw open on or after it, from the local Dolt clone,
    or None if it cannot say. The first open may be a session or two late: Dolt misses days too
    (TQQQ's 2021-01-21, the split's own ex-date), and a split still shows across the gap. Read by
    date window: `ohlcv` leads its key with `date`, so this is a short range read."""
    ex = date.fromisoformat(ex_date)
    start, end = (ex - timedelta(days=10)).isoformat(), (ex + timedelta(days=5)).isoformat()
    query = (
        "SELECT date, open, close FROM ohlcv WHERE date BETWEEN "
        f"'{start}' AND '{end}' AND act_symbol = '{symbol}' ORDER BY date"
    )
    try:
        out = subprocess.run(
            ["dolt", "sql", "-r", "json", "-q", query],
            cwd=_dolt_stocks(),
            capture_output=True,
            text=True,
            timeout=300,
            check=True,
        ).stdout
        rows = json.loads(out).get("rows") or []
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    before = [r for r in rows if r["date"][:10] < ex_date]
    after = [r for r in rows if r["date"][:10] >= ex_date]
    if not before or not after:
        return None
    prev_close, ex_open = float(before[-1]["close"]), float(after[0]["open"])
    return prev_close / ex_open if ex_open > 0 else None


def verdict(ratio: float | None, to_factor: float, for_factor: float) -> int | None:
    if ratio is None:
        return None
    expected = to_factor / for_factor
    return int(abs(ratio - expected) / expected <= TOLERANCE)


def store(conn: sqlite3.Connection, symbol: str, rows: list[tuple], fetched_at: str, source: str) -> None:
    conn.execute("DELETE FROM split_history WHERE symbol = ?", (symbol,))
    conn.executemany(
        "INSERT INTO split_history VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(symbol, d, to, fr, source, fetched_at, ratio, ok) for d, to, fr, ratio, ok in rows],
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbols", nargs="+", help="tickers to fetch")
    ap.add_argument("--dry-run", action="store_true", help="fetch, check and print; store nothing")
    ap.add_argument(
        "--declare",
        action="append",
        default=[],
        metavar="DATE:TO:FOR",
        help="declare a split instead of fetching (one symbol); checked against prices like a fetched one",
    )
    ap.add_argument("--source", help="where a declared split is recorded (required with --declare)")
    args = ap.parse_args(argv)
    if args.declare and (len(args.symbols) != 1 or not args.source):
        ap.error("--declare takes exactly one symbol and a --source")

    path = paths.split_history()
    conn = None
    if not args.dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.executescript(SCHEMA)
    failed = 0
    for i, symbol in enumerate(s.strip().upper() for s in args.symbols):
        if i:
            time.sleep(PAUSE_S)
        if args.declare:
            source = f"declared: {args.source}"
            splits = sorted(declared(args.declare))
        else:
            source = SOURCE.format(symbol=symbol.lower())
            try:
                splits = parse(fetch(symbol))
            except Exception as exc:  # noqa: BLE001 -- one symbol's failure keeps its previous rows
                print(f"{symbol}: fetch failed ({type(exc).__name__}: {exc}); previous rows kept")
                failed = 1
                continue
        if not splits:
            print(f"{symbol}: no splits on the page; previous rows kept")
            continue
        rows = []
        for d, to, fr in splits:
            ratio = raw_ratio(symbol, d)
            rows.append((d, to, fr, ratio, verdict(ratio, to, fr)))
        fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
        for d, to, fr, ratio, ok in rows:
            mark = {1: "verified", 0: "CONTRADICTED", None: "unchecked"}[ok]
            seen = f"{ratio:.3f}" if ratio is not None else "-"
            print(f"{symbol} {d} {to:g} for {fr:g}  raw ratio {seen}  {mark}")
        if conn is not None:
            store(conn, symbol, rows, fetched_at, source)
            conn.commit()
    if conn is not None:
        conn.close()
        print(f"stored in {path}")
    return failed


if __name__ == "__main__":
    sys.exit(main())
