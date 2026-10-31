"""The add-on weeklies fetch: SPXW weekly put minute greeks over each bwb position's whole life, and the
ATM calls at 10:00 for the expected move. The store behind the bwb add-on standalone study (2026-10-23)
and edge-hypotheses item 35 (the bwb module's whole rule at real quotes).

**The request file** is JSON: ``{"requests": [{"expiration", "first", "last"}, ...]}`` (a bare list
also reads; ``start`` and ``end`` are taken for ``first`` and ``last``). A request is one expiry and a
span of sessions, ``first`` through ``last``, both no later than the expiry. The sessions an expiry's
positions need are the entry days the module's ladder enters it (``clock.target_expiration``) through
its expiry day; `scripts/fetch_addon_weeklies.py requests` works out which of them the store lacks.

**The store** is ``addon_weeklies.db`` in the licensed vendor directory, the file the add-on study
read since 2026-10-23, unchanged in shape: ``q`` holds the rows (``right`` ``P`` for puts at every
strike in a band about the money, 09:30 to 16:15 each minute; ``C`` for a strip of three strikes
either side of the money at 10:00 only), ``fetched`` the old per-expiry record of a Friday's whole
life. ``fetched_spans`` is this module's record of what became of each request: ``complete`` or
``refused`` (with why). A request is written whole or not at all, in one transaction, and replaces
only its own sessions' rows, so a span for one session never touches the rest of that expiry. A run
skips a request that is complete (or covered by an old per-expiry row that starts no later), retries
a refused one, and resumes after a stop. A fatal refusal (`vendor.FATAL`) stops the run instead of
marking every remaining request refused.

Which requests to send is the study's; this module records what the vendor answered and nothing more.
"""

from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from cherrypick.core import progress as core_progress

from . import paths
from .vendor import FATAL, VendorRefusal

DB_NAME = "addon_weeklies.db"
ROOT = "SPXW"
#: Puts: strikes either side of the money, the whole regular session and the quarter hour after.
PUT_RANGE, PUT_START, PUT_END = 60, "09:30:00", "16:15:00"
#: Calls: only the 10:00 minute, a strip about the money, for the expected move.
CALL_RANGE, CALL_START, CALL_END = 3, "10:00:00", "10:01:00"
#: A put this far above the index is dropped on the way in: no leg of the structure sits there.
PUT_CEILING = 50.0
#: An old per-expiry row covers the sessions from this many days before the expiry.
LEGACY_LEAD_DAYS = 7
COMPLETE, REFUSED = "complete", "refused"

SCHEMA = """
CREATE TABLE IF NOT EXISTS q (expiration TEXT, day TEXT, ts INTEGER, right TEXT, strike REAL,
  bid REAL, ask REAL, delta REAL, iv REAL, spot REAL);
CREATE INDEX IF NOT EXISTS q_ix ON q (expiration, ts);
CREATE TABLE IF NOT EXISTS fetched (expiration TEXT PRIMARY KEY, status TEXT, rows INTEGER,
  detail TEXT, at REAL);
CREATE TABLE IF NOT EXISTS fetched_spans (expiration TEXT NOT NULL, first TEXT NOT NULL,
  last TEXT NOT NULL, status TEXT NOT NULL, rows INTEGER NOT NULL DEFAULT 0, detail TEXT,
  at REAL NOT NULL, PRIMARY KEY (expiration, first, last));
"""

Span = tuple[str, str, str]  # expiration, first session, last session


def db_path(settings: dict) -> Path:
    return paths.vendor_dir(settings) / DB_NAME


def connect(settings: dict) -> sqlite3.Connection:
    """The add-on weeklies store, created inside the marked vendor directory."""
    paths.ensure_vendor_dir(settings)
    conn = sqlite3.connect(db_path(settings), timeout=300)
    conn.executescript(SCHEMA)
    return conn


def read_requests(path: Path) -> list[Span]:
    """(expiration, first, last) in file order, each once. A span that ends before it starts, or
    after its expiry, is refused here, before any request is sent."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    items = doc["requests"] if isinstance(doc, dict) else doc
    seen: dict[Span, None] = {}
    for it in items:
        exp = str(it["expiration"])[:10]
        first = str(it.get("first") or it["start"])[:10]
        last = str(it.get("last") or it["end"])[:10]
        for d in (exp, first, last):
            date.fromisoformat(d)
        if not first <= last <= exp:
            raise ValueError(f"request span must satisfy first <= last <= expiration: {(exp, first, last)}")
        seen.setdefault((exp, first, last), None)
    return list(seen)


def _covered(conn: sqlite3.Connection, span: Span) -> bool:
    exp, first, last = span
    row = conn.execute(
        "SELECT status FROM fetched_spans WHERE expiration=? AND first=? AND last=?", span
    ).fetchone()
    if row and row[0] == COMPLETE:
        return True
    old = conn.execute("SELECT status FROM fetched WHERE expiration=?", (exp,)).fetchone()
    start = (date.fromisoformat(exp) - timedelta(days=LEGACY_LEAD_DAYS)).isoformat()
    return bool(old and old[0] == COMPLETE and start <= first and last <= exp)


def rows_from_vendor(puts: list[dict], calls: list[dict], expiration: str) -> list[tuple]:
    """The store's rows for one request from the vendor's: puts well above the index dropped, every
    value a number or None. Pure."""
    out = []
    for r in (*puts, *calls):
        spot, k = r.get("underlying"), r["strike"]
        if r["right"] == "P" and spot and k > spot + PUT_CEILING:
            continue
        out.append((expiration, r["day"], int(r["ts"]), r["right"], float(k), r.get("bid"), r.get("ask"),
                    r.get("delta"), r.get("iv"), spot))  # fmt: skip
    return out


def write_span(conn: sqlite3.Connection, span: Span, rows: list[tuple]) -> int:
    """Replace the request's sessions' rows and record it complete, in one transaction."""
    exp, first, last = span
    with conn:
        conn.execute("DELETE FROM q WHERE expiration=? AND day>=? AND day<=?", span)
        conn.executemany("INSERT INTO q VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        conn.execute(
            "INSERT OR REPLACE INTO fetched_spans VALUES (?,?,?,?,?,?,?)",
            (exp, first, last, COMPLETE, len(rows), None, time.time()),
        )
    return len(rows)


def _record_refusal(conn: sqlite3.Connection, span: Span, refusal: VendorRefusal) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO fetched_spans VALUES (?,?,?,?,?,?,?)",
            (*span, REFUSED, 0, f"{refusal.reason}: {refusal.detail}"[:300], time.time()),
        )


def _vendor_span(client, span: Span) -> list[tuple]:
    """One request's vendor calls (puts, then the 10:00 calls). Runs on a worker thread: no store."""
    exp, first, last = span
    puts = client.minute_span(ROOT, exp, first, last, right="put", start_time=PUT_START, end_time=PUT_END,
                              strike_range=PUT_RANGE)  # fmt: skip
    calls = client.minute_span(ROOT, exp, first, last, right="call", start_time=CALL_START, end_time=CALL_END,
                               strike_range=CALL_RANGE)  # fmt: skip
    return rows_from_vendor(puts, calls, exp)


def fetch_spans(
    settings: dict,
    client,
    spans: list[Span],
    *,
    workers: int = 1,
    refetch: bool = False,
    limit: int | None = None,
    stop=None,
    log=print,
) -> dict[str, Any]:
    """Fetch every request not already covered (all with `refetch`). `stop`, if given, is asked before
    each new request: once it says so nothing more is started, those in flight finish and are kept,
    and the report says `stopped`; a re-run resumes from there."""
    conn = connect(settings)
    try:
        todo = list(spans) if refetch else [s for s in spans if not _covered(conn, s)]
        if limit is not None:
            todo = todo[:limit]
        report: dict[str, Any] = {
            "asked": len(spans),
            "already": len(spans) - len([s for s in spans if refetch or not _covered(conn, s)]),
            "todo": len(todo),
            "complete": 0,
            "refused": 0,
            "rows": 0,
            "stopped": False,
        }
        rep = core_progress.Reporter("backtest-fetch-addon-weeklies")
        rep.phase("requests", total=len(spans), unit="spans")
        rep.advance("requests", report["already"])
        pending = iter(todo)
        with ThreadPoolExecutor(max(1, workers)) as ex:
            running = {}

            def submit_next() -> bool:
                if stop is not None and stop():
                    report["stopped"] = True
                    return False
                span = next(pending, None)
                if span is None:
                    return False
                running[ex.submit(_vendor_span, client, span)] = span
                return True

            for _ in range(max(1, workers)):
                if not submit_next():
                    break
            while running:
                finished, _ = wait(running, return_when=FIRST_COMPLETED)
                for fut in finished:
                    span = running.pop(fut)
                    try:
                        rows = fut.result()
                    except VendorRefusal as refusal:
                        _record_refusal(conn, span, refusal)
                        report["refused"] += 1
                        log(f"{span[0]} {span[1]}..{span[2]}: refused ({refusal.reason})")
                        if refusal.reason in FATAL:
                            report["aborted"] = f"{refusal.reason}: {refusal.detail}"[:300]
                            for other in running:
                                other.cancel()
                            running.clear()
                            break
                    else:
                        report["rows"] += write_span(conn, span, rows)
                        report["complete"] += 1
                        log(f"{span[0]} {span[1]}..{span[2]}: {len(rows)} rows")
                    rep.advance("requests", current=f"{span[0]} {span[1]}")
                    if not report.get("aborted"):
                        submit_next()
                if report.get("aborted"):
                    break
        rep.finish("failed" if report.get("aborted") else "done")
        return report
    finally:
        conn.close()


def status(settings: dict) -> dict[str, Any]:
    """What the store holds: expiries, sessions, rows and requests by status. Counts only."""
    path = db_path(settings)
    if not path.exists():
        return {"store": None}
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        span = conn.execute(
            "SELECT COUNT(DISTINCT expiration), COUNT(DISTINCT day), COUNT(*) FROM q"
        ).fetchone()
        by = dict(conn.execute("SELECT status, COUNT(*) FROM fetched_spans GROUP BY status").fetchall())
        old = dict(conn.execute("SELECT status, COUNT(*) FROM fetched GROUP BY status").fetchall())
        return {"store": str(path), "expiries": span[0], "sessions": span[1], "rows": span[2],
                "requests": by, "per_expiry": old}  # fmt: skip
    finally:
        conn.close()
