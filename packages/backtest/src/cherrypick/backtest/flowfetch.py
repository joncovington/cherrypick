"""The flow fetch: each name's day of option prints, grouped into orders (`flow.py`), the large ones
kept with the open interest either side of them. `docs/flow-signal-plan.md`, build step 1.

**What is kept,** per (name, session), in `flow.db` beside the other licensed stores:

- `flow_days`: the day's status, its print and order counts, the premium histogram of every order
  (the top-share rule's input), and how long the vendor took;
- `flow_orders`: every order at or above `flow.KEEP_PREMIUM`, whatever its side, with the contract's
  open interest at the session's start and at the next session's start.

**Open interest, declared:** the vendor reports a contract's open interest on a session's morning as
the previous session's closing figure. So an order on session D has `oi_before` from D's report and
`oi_after` from the next session's. Until that next report exists (the newest session), the day is
`awaiting_oi` and a later run fetches it again whole.

A day is written whole or not at all; a refusal writes only its `flow_days` row with the reason. The
fetch is resumable: a `complete` day is skipped unless `refetch`. A fatal refusal (another login
took the account's one session) stops it.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, date, datetime
from pathlib import Path

from cherrypick.core import calendar as _cal

from . import flow, paths
from .vendor import FATAL, VendorRefusal

DDL = (
    """CREATE TABLE IF NOT EXISTS flow_days (
        symbol TEXT NOT NULL, session TEXT NOT NULL, status TEXT NOT NULL, reason TEXT,
        prints INTEGER, single_leg_prints INTEGER, orders INTEGER, kept INTEGER, histogram TEXT,
        vendor_seconds REAL, fetched_at TEXT NOT NULL,
        PRIMARY KEY (symbol, session))""",
    """CREATE TABLE IF NOT EXISTS flow_orders (
        symbol TEXT NOT NULL, session TEXT NOT NULL, expiration TEXT NOT NULL, strike REAL NOT NULL,
        right TEXT NOT NULL, side TEXT NOT NULL, first_ms INTEGER NOT NULL, last_ms INTEGER NOT NULL,
        prints INTEGER NOT NULL, size INTEGER NOT NULL, premium REAL NOT NULL, vwap REAL NOT NULL,
        edge REAL, sweep INTEGER NOT NULL, oi_before INTEGER, oi_after INTEGER)""",
    "CREATE INDEX IF NOT EXISTS flow_orders_day ON flow_orders (symbol, session)",
)


def db_path(settings: dict) -> Path:
    return paths.vendor_dir(settings) / "flow.db"


def connect(settings: dict) -> sqlite3.Connection:
    paths.ensure_vendor_dir(settings)
    conn = sqlite3.connect(db_path(settings))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    for stmt in DDL:
        conn.execute(stmt)
    conn.commit()
    return conn


def status(conn: sqlite3.Connection, symbol: str, session: str) -> str | None:
    row = conn.execute(
        "SELECT status FROM flow_days WHERE symbol = ? AND session = ?", (symbol.upper(), session)
    ).fetchone()
    return row["status"] if row else None


def sessions(start: str, end: str) -> list[str]:
    """Trading days in [start, end]."""
    d, last, out = date.fromisoformat(start), date.fromisoformat(end), []
    if not _cal.is_trading_day(d):
        d = _cal.next_trading_day(d)
    while d <= last:
        out.append(d.isoformat())
        d = _cal.next_trading_day(d)
    return out


def build_day(prints: list[dict], oi_before: dict, oi_after: dict | None) -> dict:
    """A name-day's record from its prints and the two open-interest reports: pure."""
    for p in prints:
        p["kind"] = flow.print_kind(p["condition"])
    day_orders = flow.orders(prints)
    kept = []
    for o in day_orders:
        if o["premium"] < flow.KEEP_PREMIUM:
            continue
        key = (o["expiration"], o["right"], o["strike"])
        kept.append(
            {
                **o,
                "oi_before": oi_before.get(key),
                "oi_after": None if oi_after is None else oi_after.get(key),
            }
        )
    return {
        "status": "complete" if oi_after is not None else "awaiting_oi",
        "prints": len(prints),
        "single_leg_prints": sum(1 for p in prints if p["kind"] in ("single", "sweep")),
        "orders": len(day_orders),
        "histogram": flow.histogram(day_orders),
        "kept": kept,
    }


def write_day(conn, symbol: str, session: str, rec: dict, vendor_seconds: float) -> None:
    sym = symbol.upper()
    with conn:
        conn.execute("DELETE FROM flow_orders WHERE symbol = ? AND session = ?", (sym, session))
        conn.execute("DELETE FROM flow_days WHERE symbol = ? AND session = ?", (sym, session))
        conn.executemany(
            "INSERT INTO flow_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    sym,
                    session,
                    o["expiration"],
                    o["strike"],
                    o["right"],
                    o["side"],
                    o["first_ms"],
                    o["last_ms"],
                    o["prints"],
                    o["size"],
                    o["premium"],
                    o["vwap"],
                    o["edge"],
                    int(o["sweep"]),
                    o["oi_before"],
                    o["oi_after"],
                )  # fmt: skip
                for o in rec["kept"]
            ],
        )
        conn.execute(
            "INSERT INTO flow_days VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                sym,
                session,
                rec["status"],
                None,
                rec["prints"],
                rec["single_leg_prints"],
                rec["orders"],
                len(rec["kept"]),
                json.dumps(rec["histogram"]),
                round(vendor_seconds, 2),
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),  # fmt: skip
        )


def record_refusal(conn, symbol: str, session: str, refusal: VendorRefusal) -> None:
    sym = symbol.upper()
    with conn:
        conn.execute("DELETE FROM flow_orders WHERE symbol = ? AND session = ?", (sym, session))
        conn.execute(
            "INSERT OR REPLACE INTO flow_days (symbol, session, status, reason, fetched_at) "
            "VALUES (?,?,?,?,?)",
            (
                sym,
                session,
                "refused",
                f"{refusal.reason}: {refusal.detail}"[:300],
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )


def fetch(
    settings: dict, client, symbols: list[str], start: str, end: str, *, refetch=False, log=print
) -> dict:
    """Every (symbol, session) in the span not already complete. Sessions in order, symbols within
    each, so a stopped fetch leaves whole early sessions rather than whole early names."""
    conn = connect(settings)
    days = sessions(start, end)
    counts = {"complete": 0, "awaiting_oi": 0, "refused": 0, "skipped": 0}
    seconds = []
    try:
        for session in days:
            nxt = _cal.next_trading_day(date.fromisoformat(session)).isoformat()
            for sym in symbols:
                if not refetch and status(conn, sym, session) == "complete":
                    counts["skipped"] += 1
                    continue
                t = time.monotonic()
                try:
                    prints = client.trades(sym, session)
                    before = client.open_interest_all(sym, session)
                    try:
                        after = (
                            client.open_interest_all(sym, nxt) if nxt <= date.today().isoformat() else None
                        )
                    except VendorRefusal as r:
                        if r.reason != "no_data":
                            raise
                        after = None
                except VendorRefusal as refusal:
                    if refusal.reason in FATAL:
                        log(f"stopping: {refusal}")
                        raise
                    record_refusal(conn, sym, session, refusal)
                    counts["refused"] += 1
                    log(f"{sym} {session}: refused ({refusal.reason})")
                    continue
                took = time.monotonic() - t
                rec = build_day(prints, before, after)
                write_day(conn, sym, session, rec, took)
                counts[rec["status"]] += 1
                seconds.append(took)
                kept = len(rec["kept"])
                log(f"{sym} {session}: {rec['status']}, {rec['orders']} orders, {kept} kept, {took:.1f}s")
    finally:
        conn.close()
    return {
        **counts,
        "sessions": len(days),
        "symbols": len(symbols),
        "vendor_seconds_median": sorted(seconds)[len(seconds) // 2] if seconds else None,
        "vendor_seconds_total": round(sum(seconds), 1),
    }
