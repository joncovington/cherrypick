"""The open-interest fetch: each name's open interest per contract per session, every expiry within
`MAX_DAYS`. Item 30 (`docs/edge-hypotheses-plan.md`) builds monthly-expiry GEX walls from it.

**What is kept,** per (name, session), in `open_interest.db` beside the other licensed stores:

- `oi_days`: the day's status, its contract and expiry counts, and how long the vendor took;
- `oi_contracts`: each contract's open interest, for expiries from the session through `MAX_DAYS`
  calendar days on (a two-year LEAP is no wall a 20-60 day trade meets, and it doubles the store).

**Open interest, declared:** the vendor reports a contract's open interest on a session's morning as
the previous session's closing figure (as `flowfetch` reads it). A row for session D is that report,
so a reading at D's close uses nothing published after it.

A day is written whole or not at all; a refusal writes only its `oi_days` row with the reason. The
fetch is resumable: a `complete` day is skipped unless `refetch`. A fatal refusal (another login
took the account's one session) stops it.
"""

from __future__ import annotations

import sqlite3
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from cherrypick.core import progress as core_progress

from . import paths
from .vendor import FATAL, VendorRefusal

MAX_DAYS = 100

DDL = (
    """CREATE TABLE IF NOT EXISTS oi_days (
        symbol TEXT NOT NULL, session TEXT NOT NULL, status TEXT NOT NULL, reason TEXT,
        contracts INTEGER, expirations INTEGER, vendor_seconds REAL, fetched_at TEXT NOT NULL,
        PRIMARY KEY (symbol, session))""",
    """CREATE TABLE IF NOT EXISTS oi_contracts (
        symbol TEXT NOT NULL, session TEXT NOT NULL, expiration TEXT NOT NULL, right TEXT NOT NULL,
        strike REAL NOT NULL, open_interest INTEGER NOT NULL,
        PRIMARY KEY (symbol, session, expiration, right, strike))""",
)


def db_path(settings: dict) -> Path:
    return paths.vendor_dir(settings) / "open_interest.db"


def connect(settings: dict) -> sqlite3.Connection:
    paths.ensure_vendor_dir(settings)
    conn = sqlite3.connect(db_path(settings))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    for stmt in DDL:
        conn.execute(stmt)
    conn.commit()
    return conn


def status(conn, symbol: str, session: str) -> str | None:
    row = conn.execute(
        "SELECT status FROM oi_days WHERE symbol = ? AND session = ?", (symbol.upper(), session)
    ).fetchone()
    return row["status"] if row else None


def keep(report: dict, session: str) -> list[tuple[str, str, float, int]]:
    """The report's contracts that expire from `session` through `MAX_DAYS` on, rights as one letter:
    pure. [(expiration, right, strike, open_interest)]."""
    first = date.fromisoformat(session)
    last = (first + timedelta(days=MAX_DAYS)).isoformat()
    return sorted(
        (exp, right[0], strike, n) for (exp, right, strike), n in report.items() if session <= exp <= last
    )


def write_day(conn, symbol: str, session: str, rows: list, vendor_seconds: float) -> None:
    sym = symbol.upper()
    with conn:
        conn.execute("DELETE FROM oi_contracts WHERE symbol = ? AND session = ?", (sym, session))
        conn.executemany("INSERT INTO oi_contracts VALUES (?,?,?,?,?,?)", [(sym, session, *r) for r in rows])
        conn.execute(
            "INSERT OR REPLACE INTO oi_days VALUES (?,?,?,?,?,?,?,?)",
            (
                sym,
                session,
                "complete" if rows else "empty",
                None,
                len(rows),
                len({r[0] for r in rows}),
                round(vendor_seconds, 2),
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )


def record_refusal(conn, symbol: str, session: str, refusal: VendorRefusal) -> None:
    sym = symbol.upper()
    with conn:
        conn.execute("DELETE FROM oi_contracts WHERE symbol = ? AND session = ?", (sym, session))
        conn.execute(
            "INSERT OR REPLACE INTO oi_days (symbol, session, status, reason, fetched_at) VALUES (?,?,?,?,?)",
            (
                sym,
                session,
                "refused",
                f"{refusal.reason}: {refusal.detail}"[:300],
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )


def _vendor_day(client, sym: str, session: str) -> tuple[dict, float]:
    """One name-day's vendor call, timed. Runs on a worker thread: it touches no store."""
    t = time.monotonic()
    report = client.open_interest_all(sym, session)
    return report, time.monotonic() - t


def fetch_plan(
    settings: dict,
    client,
    plan: list[tuple[str, str]],
    *,
    workers: int = 1,
    refetch: bool = False,
    stop=None,
    log=print,
) -> dict:
    """Every (session, symbol) of `plan` not already complete, in its order, `workers` vendor calls
    at a time on the account's one session. The store is written on this thread only. `stop()`,
    when given, is asked before each new request: true lets the running ones finish and ends the
    fetch (`stopped` in the report)."""
    conn = connect(settings)
    counts = {"complete": 0, "empty": 0, "refused": 0, "skipped": 0}
    seconds: list[float] = []
    stopped = False
    try:
        todo = []
        for session, sym in plan:
            if not refetch and status(conn, sym, session) == "complete":
                counts["skipped"] += 1
            else:
                todo.append((session, sym))
        rep = core_progress.Reporter("backtest-fetch-oi")
        rep.phase("days", total=len(plan), unit="symbol-days")
        rep.advance("days", counts["skipped"])
        pending = iter(todo)
        with ThreadPoolExecutor(max(1, workers)) as ex:
            running = {}

            def submit_next() -> bool:
                nonlocal stopped
                if stop is not None and stop():
                    stopped = True
                    return False
                key = next(pending, None)
                if key is None:
                    return False
                running[ex.submit(_vendor_day, client, key[1], key[0])] = key
                return True

            for _ in range(max(1, workers)):
                if not submit_next():
                    break
            fatal = None
            while running:
                finished, _ = wait(running, return_when=FIRST_COMPLETED)
                for fut in finished:
                    session, sym = running.pop(fut)
                    try:
                        report, took = fut.result()
                    except VendorRefusal as refusal:
                        if refusal.reason in FATAL:
                            fatal = fatal or refusal
                            continue
                        record_refusal(conn, sym, session, refusal)
                        counts["refused"] += 1
                        log(f"{sym} {session}: refused ({refusal.reason})")
                    else:
                        rows = keep(report, session)
                        write_day(conn, sym, session, rows, took)
                        counts["complete" if rows else "empty"] += 1
                        seconds.append(took)
                        log(f"{sym} {session}: {len(rows)} contracts, {took:.1f}s")
                    rep.advance("days", current=f"{sym} {session}")
                    if fatal is None:
                        submit_next()
            if fatal is not None:
                log(f"stopping: {fatal}")
                rep.note(f"stopping: {fatal}")
                rep.finish("failed")
                raise fatal
            rep.finish("done")
    finally:
        conn.close()
    return {
        **counts,
        "planned": len(plan),
        "stopped": stopped,
        "vendor_seconds_median": sorted(seconds)[len(seconds) // 2] if seconds else None,
        "vendor_seconds_total": round(sum(seconds), 1),
    }
