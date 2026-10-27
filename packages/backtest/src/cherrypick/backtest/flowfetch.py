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
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, date, datetime
from pathlib import Path

from cherrypick.core import calendar as _cal
from cherrypick.core import progress as core_progress

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


def _vendor_day(client, sym: str, session: str) -> tuple[list[dict], dict, dict | None, float]:
    """One name-day's three vendor calls, timed. Runs on a worker thread: it touches no store."""
    t = time.monotonic()
    nxt = _cal.next_trading_day(date.fromisoformat(session)).isoformat()
    prints = client.trades(sym, session)
    before = client.open_interest_all(sym, session)
    try:
        after = client.open_interest_all(sym, nxt) if nxt <= date.today().isoformat() else None
    except VendorRefusal as r:
        if r.reason != "no_data":
            raise
        after = None
    return prints, before, after, time.monotonic() - t


def read_plan(path: Path) -> list[tuple[str, str]]:
    """A plan file's (session, symbol) pairs in fetch order: {"sessions": {session: [symbols]}}."""
    body = json.loads(Path(path).read_text(encoding="utf-8"))
    return [(d, s.upper()) for d in sorted(body["sessions"]) for s in body["sessions"][d]]


def fetch(
    settings: dict, client, symbols: list[str], start: str, end: str, *, refetch=False, log=print
) -> dict:
    """Every (symbol, session) in the span not already complete. Sessions in order, symbols within
    each, so a stopped fetch leaves whole early sessions rather than whole early names."""
    days = sessions(start, end)
    report = fetch_plan(settings, client, [(d, s) for d in days for s in symbols], refetch=refetch, log=log)
    return {**report, "sessions": len(days), "symbols": len(symbols)}


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
    fetch (`stopped` in the report), so it can stand clear of the nightly fetch's login."""
    conn = connect(settings)
    counts = {"complete": 0, "awaiting_oi": 0, "refused": 0, "skipped": 0}
    seconds: list[float] = []
    stopped = False
    try:
        todo = []
        for session, sym in plan:
            if not refetch and status(conn, sym, session) == "complete":
                counts["skipped"] += 1
            else:
                todo.append((session, sym))
        rep = core_progress.Reporter("backtest-fetch-flow")
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
                        prints, before, after, took = fut.result()
                    except VendorRefusal as refusal:
                        if refusal.reason in FATAL:
                            fatal = fatal or refusal
                            continue
                        record_refusal(conn, sym, session, refusal)
                        counts["refused"] += 1
                        log(f"{sym} {session}: refused ({refusal.reason})")
                    else:
                        rec = build_day(prints, before, after)
                        write_day(conn, sym, session, rec, took)
                        counts[rec["status"]] += 1
                        seconds.append(took)
                        n, kept = rec["orders"], len(rec["kept"])
                        log(f"{sym} {session}: {rec['status']}, {n} orders, {kept} kept, {took:.1f}s")
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
