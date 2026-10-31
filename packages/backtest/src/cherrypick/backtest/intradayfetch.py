"""Intraday single-name option chains, one (name, session, expiry, window) at a time: the event-driven
fetch behind edge-hypotheses item 31 (the earnings module's paper rule).

**Why a store of its own.** `chainfetch` keeps end-of-day quotes (a bid, an ask and a close per
contract). Item 31 enters at the 15:40 ET bar the session before a report and marks a held position
on a 5-minute grid, so it needs the quote, the delta, the implied volatility and the underlying's
price at each bar. That is `ThetaDataClient.chain_window`; this module is what keeps its answers.

**The request file** is JSON: ``{"requests": [{"symbol", "session", "expiration", "start", "end",
"interval"?}, ...]}`` (a bare list also reads). ``start`` and ``end`` are "HH:MM" ET, ``interval``
defaults to ``5m``. Its order is kept; a repeated request is fetched once.

**The store** is `intraday_chains.db` in the licensed vendor directory (`paths.ensure_vendor_dir`,
marker first): `intraday_chain_quotes` holds the rows, `intraday_chain_requests` what became of each
request -- `complete` (rows written), `empty` (the vendor holds nothing for it) or `refused` (with
why). A run skips complete and empty requests and retries refused ones, so it is resumable and a
re-run is cheap; `refetch` replaces everything asked for. A fatal refusal (`vendor.FATAL`: the
session was taken by another login) stops the run instead of marking every remaining request refused.
A request is written whole or not at all, in one transaction. Only the rows inside the requested
window are kept.

Which requests the client should send, and what a complete one is (the pilot's coverage test), are
the study's: `scripts/earnings_item31.py`. This module records what the vendor answered and nothing
more.
"""

from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from cherrypick.core import progress as core_progress

from . import paths
from .chainfetch import vendor_root
from .vendor import FATAL, VendorRefusal

DB_NAME = "intraday_chains.db"
#: The vendor's way of saying it holds nothing for a request: an empty chain, not a refusal to retry.
NO_DATA = "no_data"
COMPLETE, EMPTY, REFUSED = "complete", "empty", "refused"
DEFAULT_INTERVAL = "5m"

SCHEMA = """
CREATE TABLE IF NOT EXISTS intraday_chain_quotes (
    symbol TEXT NOT NULL, session TEXT NOT NULL, expiration TEXT NOT NULL, bar TEXT NOT NULL,
    strike REAL NOT NULL, right TEXT NOT NULL,
    bid REAL, ask REAL, delta REAL, iv REAL, underlying REAL,
    PRIMARY KEY (symbol, session, expiration, bar, strike, right));
CREATE TABLE IF NOT EXISTS intraday_chain_requests (
    symbol TEXT NOT NULL, session TEXT NOT NULL, expiration TEXT NOT NULL,
    start TEXT NOT NULL, "end" TEXT NOT NULL, interval TEXT NOT NULL,
    status TEXT NOT NULL, rows INTEGER NOT NULL DEFAULT 0, bars INTEGER NOT NULL DEFAULT 0,
    detail TEXT, fetched_at REAL NOT NULL,
    PRIMARY KEY (symbol, session, expiration, start, "end", interval));
"""

Key = tuple[str, str, str, str, str, str]  # symbol, session, expiration, start, end, interval


def db_path(settings: dict) -> Path:
    return paths.vendor_dir(settings) / DB_NAME


def connect(settings: dict) -> sqlite3.Connection:
    """The intraday chain store, created inside the marked vendor directory."""
    paths.ensure_vendor_dir(settings)
    conn = sqlite3.connect(db_path(settings), timeout=300)
    conn.executescript(SCHEMA)
    return conn


def _hhmm(text: str) -> str:
    t = str(text).strip()
    hh, _, mm = t.partition(":")
    if not (len(mm) == 2 and hh.isdigit() and mm.isdigit() and 0 <= int(hh) < 24 and int(mm) < 60):
        raise ValueError(f"not an HH:MM time: {text!r}")
    return f"{int(hh):02d}:{mm}"


def read_requests(path: Path) -> list[Key]:
    """(SYMBOL, session, expiration, start, end, interval) in file order, each once. A window that
    ends before it starts is refused here, before any request is sent."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    items = doc["requests"] if isinstance(doc, dict) else doc
    seen: dict[Key, None] = {}
    for it in items:
        key = (
            str(it["symbol"]).upper(),
            str(it["session"])[:10],
            str(it["expiration"])[:10],
            _hhmm(it["start"]),
            _hhmm(it["end"]),
            str(it.get("interval") or DEFAULT_INTERVAL),
        )
        if key[3] > key[4]:
            raise ValueError(f"request window ends before it starts: {key}")
        seen.setdefault(key, None)
    return list(seen)


def _done(conn: sqlite3.Connection) -> set[Key]:
    return {
        tuple(r)
        for r in conn.execute(
            'SELECT symbol, session, expiration, start, "end", interval FROM intraday_chain_requests '
            "WHERE status IN (?, ?)",
            (COMPLETE, EMPTY),
        )
    }


def _record(
    conn: sqlite3.Connection, key: Key, status: str, rows: int, bars: int, detail: str | None
) -> None:
    conn.execute(
        'INSERT OR REPLACE INTO intraday_chain_requests (symbol, session, expiration, start, "end", '
        "interval, status, rows, bars, detail, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (*key, status, rows, bars, detail, time.time()),
    )


def write_window(conn: sqlite3.Connection, key: Key, rows: list[dict]) -> int:
    """Replace one request's rows and record it, in one transaction. Only rows whose bar lies in the
    requested window are kept. Returns the rows kept."""
    sym, day, exp, start, end, _interval = key
    kept = [r for r in rows if start <= r["bar"] <= end]
    with conn:
        conn.execute(
            "DELETE FROM intraday_chain_quotes WHERE symbol = ? AND session = ? AND expiration = ? "
            "AND bar >= ? AND bar <= ?",
            (sym, day, exp, start, end),
        )
        conn.executemany(
            "INSERT OR REPLACE INTO intraday_chain_quotes "
            "(symbol, session, expiration, bar, strike, right, bid, ask, delta, iv, underlying) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (sym, day, exp, r["bar"], r["strike"], r["right"], r.get("bid"), r.get("ask"),
                 r.get("delta"), r.get("iv"), r.get("underlying"))
                for r in kept
            ],
        )  # fmt: skip
        _record(conn, key, COMPLETE if kept else EMPTY, len(kept), len({r["bar"] for r in kept}), None)
    return len(kept)


def fetch_windows(
    settings: dict,
    client,
    requests: list[Key],
    *,
    workers: int = 1,
    refetch: bool = False,
    limit: int | None = None,
    stop=None,
    log=print,
) -> dict[str, Any]:
    """Fetch every request not already complete or empty (all of them with `refetch`). `stop`, if
    given, is asked before each new request: once it says so, nothing more is started, the requests
    in flight finish and are kept, and the report says `stopped` -- a re-run resumes from there."""
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
            "stopped": False,
        }
        started = time.monotonic()
        rep = core_progress.Reporter("backtest-fetch-intraday-chains")
        rep.phase("requests", total=len(requests), unit="windows")
        rep.advance("requests", report["already"])
        pending = iter(todo)
        with ThreadPoolExecutor(max(1, workers)) as ex:
            running = {}

            def submit_next() -> bool:
                if stop is not None and stop():
                    report["stopped"] = True
                    return False
                key = next(pending, None)
                if key is None:
                    return False
                sym, day, exp, start, end, interval = key
                running[
                    ex.submit(client.chain_window, vendor_root(sym), day, exp, start, end, interval=interval)
                ] = key
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
                        if refusal.reason == NO_DATA:
                            write_window(conn, key, [])
                            report["empty"] += 1
                        else:
                            with conn:
                                _record(conn, key, REFUSED, 0, 0, f"{refusal.reason}: {refusal.detail}"[:300])
                            report["refused"] += 1
                        if refusal.reason in FATAL:
                            report["aborted"] = f"{refusal.reason}: {refusal.detail}"[:300]
                            for other in running:
                                other.cancel()
                            running.clear()
                            break
                    except Exception as exc:  # noqa: BLE001 -- one request's failure is that request's
                        with conn:
                            _record(conn, key, REFUSED, 0, 0, f"{type(exc).__name__}: {exc}"[:300])
                        report["refused"] += 1
                    else:
                        n = write_window(conn, key, rows)
                        report["complete" if n else "empty"] += 1
                        report["rows"] += n
                    rep.advance("requests", current=f"{key[0]} {key[1]}")
                    if not report.get("aborted"):
                        submit_next()
                done_n = report["complete"] + report["empty"] + report["refused"]
                if done_n and done_n % 100 == 0:
                    rate = done_n / max(1e-9, time.monotonic() - started)
                    log(f"intraday {done_n}/{len(todo)} ({rate:.2f}/s), {report['refused']} refused")
                if report.get("aborted"):
                    break
        report["seconds"] = round(time.monotonic() - started, 1)
        if report.get("stopped"):
            rep.note("stopped: resumes from here on the next run")
        rep.finish("failed" if report.get("aborted") else "done")
        return report
    finally:
        conn.close()


def status(settings: dict) -> dict[str, Any]:
    """What the store holds: requests by status, rows, names and sessions. Counts only."""
    path = db_path(settings)
    if not path.exists():
        return {"store": None}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        by = dict(
            conn.execute("SELECT status, COUNT(*) FROM intraday_chain_requests GROUP BY status").fetchall()
        )
        span = conn.execute(
            "SELECT MIN(session), MAX(session), COUNT(DISTINCT symbol) FROM intraday_chain_requests"
        ).fetchone()
        rows = conn.execute("SELECT COUNT(*) FROM intraday_chain_quotes").fetchone()[0]
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
