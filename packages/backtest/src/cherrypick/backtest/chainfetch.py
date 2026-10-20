"""End-of-day option chains, one (name, session, expiry) at a time: the event-driven fetch.

**Why event-driven.** A replay that holds a spread to expiry needs only the chain on the day it was
opened: the expiry value comes from the stock's close. So instead of every name's every chain every
session -- weeks of requests at the account's tier, which serves single stocks from 2016 at about a
second a session-chain and has no whole-market daily file -- a study writes the list of entry
sessions it needs, and this fetches exactly those (docs/edge-hypotheses-plan.md, item 14).

**The request file** is JSON: ``{"requests": [{"symbol", "session", "expiration"}, ...]}`` (a bare
list also reads). Its order is kept; a repeated request is fetched once.

**The store** is `eod_chains.db` in the licensed vendor directory (`paths.ensure_vendor_dir`, marker
first): `eod_chain_quotes` holds the rows, `eod_chain_requests` what became of each request --
`complete` (rows written), `empty` (the vendor holds nothing for it) or `refused` (with why). A run
skips complete and empty requests and retries refused ones, so it is resumable and a re-run is
cheap; `refetch` replaces everything asked for. A fatal refusal (`vendor.FATAL`: the session was
taken by another login) stops the run instead of marking every remaining request refused.

**Workers** share the one client (the account allows one session); every database write happens on
this thread, so a request is either wholly written or not written at all.
"""

from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from . import paths
from .vendor import FATAL, VendorRefusal

DB_NAME = "eod_chains.db"
COMPLETE, EMPTY, REFUSED = "complete", "empty", "refused"

SCHEMA = """
CREATE TABLE IF NOT EXISTS eod_chain_quotes (
    symbol TEXT NOT NULL, session TEXT NOT NULL, expiration TEXT NOT NULL,
    strike REAL NOT NULL, right TEXT NOT NULL,
    bid REAL, ask REAL, close REAL, volume REAL, bid_size REAL, ask_size REAL,
    PRIMARY KEY (symbol, session, expiration, strike, right));
CREATE TABLE IF NOT EXISTS eod_chain_requests (
    symbol TEXT NOT NULL, session TEXT NOT NULL, expiration TEXT NOT NULL,
    status TEXT NOT NULL, rows INTEGER NOT NULL DEFAULT 0, detail TEXT, fetched_at REAL NOT NULL,
    PRIMARY KEY (symbol, session, expiration));
"""


def db_path(settings: dict) -> Path:
    return paths.vendor_dir(settings) / DB_NAME


def connect(settings: dict) -> sqlite3.Connection:
    """The chain store, created inside the marked vendor directory."""
    paths.ensure_vendor_dir(settings)
    conn = sqlite3.connect(db_path(settings))
    conn.executescript(SCHEMA)
    return conn


def read_requests(path: Path) -> list[tuple[str, str, str]]:
    """(SYMBOL, session, expiration) in file order, each once."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    items = doc["requests"] if isinstance(doc, dict) else doc
    seen: dict[tuple[str, str, str], None] = {}
    for it in items:
        key = (str(it["symbol"]).upper(), str(it["session"])[:10], str(it["expiration"])[:10])
        seen.setdefault(key, None)
    return list(seen)


def _done(conn: sqlite3.Connection) -> set[tuple[str, str, str]]:
    return {
        (s, d, e)
        for s, d, e in conn.execute(
            "SELECT symbol, session, expiration FROM eod_chain_requests WHERE status IN (?, ?)",
            (COMPLETE, EMPTY),
        )
    }


def write_chain(conn: sqlite3.Connection, key: tuple[str, str, str], rows: list[dict]) -> int:
    """Replace one request's rows and record it, in one transaction."""
    sym, day, exp = key
    with conn:
        conn.execute(
            "DELETE FROM eod_chain_quotes WHERE symbol = ? AND session = ? AND expiration = ?",
            (sym, day, exp),
        )
        conn.executemany(
            "INSERT OR REPLACE INTO eod_chain_quotes "
            "(symbol, session, expiration, strike, right, bid, ask, close, volume, bid_size, ask_size) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (sym, day, exp, r["strike"], r["right"], r.get("bid"), r.get("ask"), r.get("close"),
                 r.get("volume"), r.get("bid_size"), r.get("ask_size"))
                for r in rows
            ],
        )  # fmt: skip
        _record(conn, key, COMPLETE if rows else EMPTY, len(rows), None)
    return len(rows)


def _record(conn, key, status: str, rows: int, detail: str | None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO eod_chain_requests "
        "(symbol, session, expiration, status, rows, detail, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (*key, status, rows, detail, time.time()),
    )


def fetch_chains(
    settings: dict,
    client,
    requests: list[tuple[str, str, str]],
    *,
    workers: int = 1,
    refetch: bool = False,
    limit: int | None = None,
    log=print,
) -> dict[str, Any]:
    """Fetch every request not already complete or empty (all of them with `refetch`)."""
    conn = connect(settings)
    try:
        done = set() if refetch else _done(conn)
        todo = [k for k in requests if k not in done]
        if limit is not None:
            todo = todo[:limit]
        report: dict[str, Any] = {
            "asked": len(requests),
            "already": len(requests) - len([k for k in requests if k not in done]),
            "todo": len(todo),
            "complete": 0,
            "empty": 0,
            "refused": 0,
            "rows": 0,
        }
        started = time.monotonic()
        pending = iter(todo)
        with ThreadPoolExecutor(max(1, workers)) as ex:
            running = {}

            def submit_next() -> bool:
                key = next(pending, None)
                if key is None:
                    return False
                running[ex.submit(client.eod_chain, key[0], key[1], key[2])] = key
                return True

            for _ in range(max(1, workers)):
                if not submit_next():
                    break
            while running:
                finished, _ = wait(running, return_when=FIRST_COMPLETED)
                for fut in finished:
                    key = running.pop(fut)
                    try:
                        rows = fut.result()
                    except VendorRefusal as refusal:
                        with conn:
                            _record(conn, key, REFUSED, 0, f"{refusal.reason}: {refusal.detail}"[:300])
                        report["refused"] += 1
                        if refusal.reason in FATAL:
                            report["aborted"] = f"{refusal.reason}: {refusal.detail}"[:300]
                            for other in running:
                                other.cancel()
                            running.clear()
                            break
                        continue
                    except Exception as exc:  # noqa: BLE001 -- one request's failure is that request's
                        with conn:
                            _record(conn, key, REFUSED, 0, f"{type(exc).__name__}: {exc}"[:300])
                        report["refused"] += 1
                    else:
                        n = write_chain(conn, key, rows)
                        report["complete" if n else "empty"] += 1
                        report["rows"] += n
                    if not report.get("aborted"):
                        submit_next()
                done_n = report["complete"] + report["empty"] + report["refused"]
                if done_n and done_n % 500 == 0:
                    rate = done_n / max(1e-9, time.monotonic() - started)
                    log(f"chains {done_n}/{len(todo)} ({rate:.2f}/s), {report['refused']} refused")
                if report.get("aborted"):
                    break
        report["seconds"] = round(time.monotonic() - started, 1)
        return report
    finally:
        conn.close()


def status(settings: dict) -> dict[str, Any]:
    """What the chain store holds: requests by status, rows, names and sessions."""
    path = db_path(settings)
    if not path.exists():
        return {"store": None}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        by = dict(conn.execute("SELECT status, COUNT(*) FROM eod_chain_requests GROUP BY status").fetchall())
        span = conn.execute(
            "SELECT MIN(session), MAX(session), COUNT(DISTINCT symbol) FROM eod_chain_requests"
        ).fetchone()
        rows = conn.execute("SELECT COUNT(*) FROM eod_chain_quotes").fetchone()[0]
        return {
            "store": str(path),
            "requests": by,
            "rows": rows,
            "first": span[0],
            "last": span[1],
            "names": span[2],
        }
    finally:
        conn.close()
