"""End-of-day option chains, one (name, session, expiry) at a time: the event-driven fetch.

**Why event-driven.** A replay that holds a spread to expiry needs only the chain on the day it was
opened: the expiry value comes from the stock's close. So instead of every name's every chain every
session -- weeks of requests at the account's tier, which serves single stocks from 2016 at about a
second a session-chain and has no whole-market daily file -- a study writes the list of entry
sessions it needs, and this fetches exactly those (research/edge-hypotheses-plan.md, item 14).

**The request file** is JSON: ``{"requests": [{"symbol", "session", "expiration"}, ...]}`` (a bare
list also reads). Its order is kept; a repeated request is fetched once.

**The store** is `eod_chains.db` in the licensed vendor directory (`paths.ensure_vendor_dir`, marker
first): `eod_chain_quotes` holds the rows, `eod_chain_requests` what became of each request --
`complete` (rows written), `empty` (the vendor holds nothing for it, its `no_data` included) or
`refused` (with why). `eod_chain_paths` records each daily path, and `eod_chain_days` each session
a path fetch asked for, with every expiry the vendor's answer held. A run
skips complete and empty requests and retries refused ones, so it is resumable and a re-run is
cheap; `refetch` replaces everything asked for. A fatal refusal (`vendor.FATAL`: the session was
taken by another login) stops the run instead of marking every remaining request refused.

**Settled refusals.** Some refusals are the vendor's fixed answer, the same on every retry
(`SETTLED`: `stream_removed`, which it gives every time for history it does not serve, and
`permission_denied`, the account's tier). A request refused again with the same settled reason it
was refused with before is counted `settled` in the report, so the research queue can tell a pass
with nothing left the vendor will serve from one that fetched nothing it could have.

**Workers** share the one client (the account allows one session); every database write happens on
this thread, so a request is either wholly written or not written at all.
"""

from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from cherrypick.core import calendar as cal
from cherrypick.core import progress as core_progress

from . import paths
from .vendor import FATAL, VendorRefusal

DB_NAME = "eod_chains.db"
#: The vendor's way of saying it holds nothing for a request: an empty chain, not a refusal to retry.
NO_DATA = "no_data"
COMPLETE, EMPTY, REFUSED = "complete", "empty", "refused"
#: Refusals the vendor repeats on every retry (see the module docstring): retrying cannot change them.
SETTLED = frozenset({"stream_removed", "permission_denied"})

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
CREATE TABLE IF NOT EXISTS eod_chain_paths (
    symbol TEXT NOT NULL, expiration TEXT NOT NULL, start TEXT NOT NULL, through TEXT NOT NULL,
    status TEXT NOT NULL, sessions INTEGER NOT NULL DEFAULT 0, rows INTEGER NOT NULL DEFAULT 0,
    detail TEXT, fetched_at REAL NOT NULL,
    PRIMARY KEY (symbol, expiration, start, through));
CREATE TABLE IF NOT EXISTS eod_chain_days (
    symbol TEXT NOT NULL, session TEXT NOT NULL, status TEXT NOT NULL, expirations TEXT,
    rows INTEGER NOT NULL DEFAULT 0, detail TEXT, fetched_at REAL NOT NULL,
    PRIMARY KEY (symbol, session));
"""


def db_path(settings: dict) -> Path:
    return paths.vendor_dir(settings) / DB_NAME


def connect(settings: dict) -> sqlite3.Connection:
    """The chain store, created inside the marked vendor directory."""
    paths.ensure_vendor_dir(settings)
    # another study reading the store, or a second fetch writing it, holds the lock for a moment;
    # sqlite's 5 s default ended a 1,500-path run mid-write (2026-10-23)
    conn = sqlite3.connect(db_path(settings), timeout=300)
    conn.executescript(SCHEMA)
    return conn


def vendor_root(symbol: str) -> str:
    """The vendor's option root for a study's symbol: a share class has no separator (BRK.B is BRKB,
    checked 2026-10-20: BRK.B and BRK/B return no data). The store keeps the study's spelling."""
    return symbol.replace(".", "").replace("/", "")


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


def _reason(detail: str | None) -> str | None:
    """A recorded refusal's reason: the text before its first colon."""
    return detail.split(":", 1)[0].strip() if detail else None


def prior_refusals(conn: sqlite3.Connection, table: str, keys: list[tuple]) -> dict[tuple, str]:
    """The reason each of `keys` was last refused with, for the keys refused before this run.
    `table` is `eod_chain_requests` (keys symbol, session, expiration) or `eod_chain_paths` (symbol,
    expiration, start, through)."""
    cols = {
        "eod_chain_requests": "symbol, session, expiration",
        "eod_chain_paths": "symbol, expiration, start, through",
    }[table]
    want = set(keys)
    return {
        tuple(r[:-1]): _reason(r[-1])
        for r in conn.execute(f"SELECT {cols}, detail FROM {table} WHERE status = ?", (REFUSED,))
        if tuple(r[:-1]) in want
    }


def settled(refusal: VendorRefusal, key: tuple, prior: dict[tuple, str]) -> bool:
    """Whether this refusal is the vendor's fixed answer again: a settled reason, the same one the
    request was refused with before."""
    return refusal.reason in SETTLED and prior.get(key) == refusal.reason


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
        prior = prior_refusals(conn, "eod_chain_requests", todo)
        report: dict[str, Any] = {
            "asked": len(requests),
            "already": len(requests) - len([k for k in requests if k not in done]),
            "todo": len(todo),
            "complete": 0,
            "empty": 0,
            "refused": 0,
            "settled": 0,
            "rows": 0,
            "stopped": False,
        }
        started = time.monotonic()
        # Answered of the whole request list, as `progress` below counts it: a resumed fetch starts
        # where the last one stopped rather than at zero.
        rep = core_progress.Reporter("backtest-fetch-chains")
        rep.split(
            "requests", (k[0] for k in requests), unit="chains", done=(k[0] for k in requests if k in done)
        )
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
                running[ex.submit(client.eod_chain, vendor_root(key[0]), key[1], key[2])] = key
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
                            write_chain(conn, key, [])
                            report["empty"] += 1
                        else:
                            with conn:
                                _record(conn, key, REFUSED, 0, f"{refusal.reason}: {refusal.detail}"[:300])
                            report["refused"] += 1
                            report["settled"] += settled(refusal, key, prior)
                        if refusal.reason in FATAL:
                            report["aborted"] = f"{refusal.reason}: {refusal.detail}"[:300]
                            for other in running:
                                other.cancel()
                            running.clear()
                            break
                    except Exception as exc:  # noqa: BLE001 -- one request's failure is that request's
                        with conn:
                            _record(conn, key, REFUSED, 0, f"{type(exc).__name__}: {exc}"[:300])
                        report["refused"] += 1
                    else:
                        n = write_chain(conn, key, rows)
                        report["complete" if n else "empty"] += 1
                        report["rows"] += n
                    rep.advance("requests", current=f"{key[0]} {key[1]}", key=key[0])
                    if not report.get("aborted"):
                        submit_next()
                done_n = report["complete"] + report["empty"] + report["refused"]
                if done_n and done_n % 500 == 0:
                    rate = done_n / max(1e-9, time.monotonic() - started)
                    log(f"chains {done_n}/{len(todo)} ({rate:.2f}/s), {report['refused']} refused")
                if report.get("aborted"):
                    break
        report["seconds"] = round(time.monotonic() - started, 1)
        if report.get("stopped"):
            rep.note("stopped: resumes from here on the next run")
        rep.finish("failed" if report.get("aborted") else "done")
        return report
    finally:
        conn.close()


def read_path_requests(path: Path) -> list[tuple[str, str, str, str]]:
    """(SYMBOL, expiration, start, through) in file order, each once. The file is
    ``{"requests": [{"symbol", "expiration", "start", "through"}, ...]}`` (a bare list also reads)."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    items = doc["requests"] if isinstance(doc, dict) else doc
    seen: dict[tuple[str, str, str, str], None] = {}
    for it in items:
        key = (str(it["symbol"]).upper(), str(it["expiration"])[:10], str(it["start"])[:10],
               str(it["through"])[:10])  # fmt: skip
        if key[2] > key[3]:
            raise ValueError(f"path request starts after it ends: {key}")
        seen.setdefault(key, None)
    return list(seen)


def path_days(key: tuple[str, str, str, str]) -> list[str]:
    """The trading sessions a path covers: from its start through its end or its expiry, whichever
    comes first (nothing trades after the expiry)."""
    _sym, exp, start, through = key
    d, last = date.fromisoformat(start), min(date.fromisoformat(through), date.fromisoformat(exp))
    out = []
    while d <= last:
        if cal.is_trading_day(d):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _held(conn: sqlite3.Connection, symbols: set[str]) -> tuple[dict, dict]:
    """What the store already answers for these symbols: each chain's (status, rows) from
    `eod_chain_requests`, and each answered day's expiries from `eod_chain_days`."""
    chains: dict[tuple[str, str, str], tuple[str, int]] = {}
    listed: dict[tuple[str, str], frozenset[str]] = {}
    for sym in symbols:
        for day, exp, st, n in conn.execute(
            "SELECT session, expiration, status, rows FROM eod_chain_requests "
            "WHERE symbol = ? AND status IN (?, ?)",
            (sym, COMPLETE, EMPTY),
        ):
            chains[(sym, day, exp)] = (st, int(n or 0))
        for day, exps in conn.execute(
            "SELECT session, expirations FROM eod_chain_days WHERE symbol = ? AND status = ?", (sym, COMPLETE)
        ):
            listed[(sym, day)] = frozenset(json.loads(exps or "[]"))
    return chains, listed


#: What one path holds on one of its sessions: rows of its chain, absent from an answered day (the
#: expiry was not in that session's chains), the vendor holding nothing for the whole day, or a refusal.
ANSWERED, ABSENT, NODATA = "answered", "absent", "nodata"


def judge_path(outcomes: list[tuple[str, Any]]) -> tuple[str, int, int, str | None]:
    """A path's record from its sessions' outcomes, in date order: (status, sessions, rows, detail).
    Pure. A refused session refuses the path with its reason. With none answered the path is empty,
    as a vendor `no_data` over the whole range was. A day the vendor answered nothing for, AFTER a
    session of the path answered, is a hole (`path_gap`): the path is refused and retried rather than
    stored with a gap. Days before the first answer are the expiry not listed yet. An expiry absent
    from a day the vendor answered is that session's answer, not a hole."""
    for kind, value in outcomes:
        if kind == REFUSED:
            return REFUSED, 0, 0, value
    seen = False
    sessions = rows = 0
    for i, (kind, value) in enumerate(outcomes):
        if kind == ANSWERED:
            seen = True
            sessions += 1
            rows += value
        elif kind == NODATA and seen:
            return REFUSED, 0, 0, f"path_gap: the vendor held nothing for session {i + 1} of {len(outcomes)}"
    return (COMPLETE, sessions, rows, None) if rows else (EMPTY, 0, 0, None)


def write_day(
    conn: sqlite3.Connection, sym: str, day: str, chains: dict[str, list[dict]], wanted: set[str]
) -> dict[str, int]:
    """One session's answer: the wanted expiries' chains written (each recorded complete, so a
    single-session reader sees it) and the day recorded with every expiry it held, all in one
    transaction. Returns {expiration: rows} for the wanted expiries it held."""
    kept: dict[str, int] = {}
    with conn:
        for exp in sorted(wanted & chains.keys()):
            rows = chains[exp]
            if not rows:
                continue
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
            _record(conn, (sym, day, exp), COMPLETE, len(rows), None)
            kept[exp] = len(rows)
        held = sorted(e for e, r in chains.items() if r)
        _record_day(conn, (sym, day), COMPLETE, held, sum(kept.values()), None)
    return kept


def _record_day(conn, key, status: str, expirations: list[str], rows: int, detail: str | None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO eod_chain_days "
        "(symbol, session, status, expirations, rows, detail, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (*key, status, json.dumps(expirations), rows, detail, time.time()),
    )


def _record_path(conn, key, status: str, sessions: int, rows: int, detail: str | None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO eod_chain_paths "
        "(symbol, expiration, start, through, status, sessions, rows, detail, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (*key, status, sessions, rows, detail, time.time()),
    )


def fetch_paths(
    settings: dict,
    client,
    requests: list[tuple[str, str, str, str]],
    *,
    workers: int = 1,
    refetch: bool = False,
    limit: int | None = None,
    stop=None,
    log=print,
) -> dict[str, Any]:
    """The daily path of each expiry over [start, through]: what a replay needs to mark a held
    contract every session, not only on the day it was opened.

    Asked by session, not by expiry: one vendor call (`eod_chain_day`) answers every expiry a
    symbol had on one session, for about what one expiry costs, and a session several paths cover
    is asked once. Only the expiries some path wants are kept. A session the store already answers
    for a path (its chain recorded, or the day recorded without that expiry) is not asked again. A
    path is recorded as soon as every one of its sessions is answered (`judge_path`); a stopped or
    aborted run leaves the rest unrecorded, and the next run asks only what they still lack.
    Resumable, stopped by a fatal refusal, and by `stop` as `fetch_chains` is. Counts are paths, as
    the research queue reads them; `days` counts the vendor calls."""
    conn = connect(settings)
    try:
        done: set = set()
        if not refetch:
            done = set(
                conn.execute(
                    "SELECT symbol, expiration, start, through FROM eod_chain_paths WHERE status IN (?, ?)",
                    (COMPLETE, EMPTY),
                )
            )
        todo = [k for k in requests if k not in done]
        prior = prior_refusals(conn, "eod_chain_paths", todo)
        report: dict[str, Any] = {
            "asked": len(requests),
            "already": len(requests) - len(todo),
            "todo": len(todo) if limit is None else min(limit, len(todo)),
            "complete": 0,
            "empty": 0,
            "refused": 0,
            "settled": 0,
            "sessions": 0,
            "rows": 0,
            "days": {"asked": 0, "answered": 0, "no_data": 0, "refused": 0},
            "stopped": False,
        }
        if limit is not None:
            todo = todo[:limit]
        started = time.monotonic()
        rep = core_progress.Reporter("backtest-fetch-chain-paths")
        rep.split(
            "paths", (k[0] for k in requests), unit="expiries", done=(k[0] for k in requests if k in done)
        )
        chains, listed = ({}, {}) if refetch else _held(conn, {k[0] for k in todo})

        # Each open path's sessions, what each already holds, and the days still to ask.
        outcome: dict[tuple, dict[str, tuple[str, Any]]] = {}
        waiting: dict[tuple, int] = {}
        wanted: dict[tuple[str, str], set[str]] = {}
        paths_on: dict[tuple[str, str], list[tuple]] = {}
        order: dict[tuple[str, str], None] = {}
        for key in todo:
            sym, exp = key[0], key[1]
            outcome[key] = {}
            for day in path_days(key):
                held = chains.get((sym, day, exp))
                if held is not None:
                    outcome[key][day] = (ANSWERED, held[1]) if held[0] == COMPLETE else (ABSENT, 0)
                elif (sym, day) in listed and exp not in listed[(sym, day)]:
                    outcome[key][day] = (ABSENT, 0)
                else:
                    wanted.setdefault((sym, day), set()).add(exp)
                    paths_on.setdefault((sym, day), []).append(key)
                    order.setdefault((sym, day), None)
                    waiting[key] = waiting.get(key, 0) + 1

        def settle(key) -> None:
            """Record a path whose every session is answered."""
            days = outcome.pop(key)
            status, sessions, rows, detail = judge_path([days[d] for d in sorted(days)])
            with conn:
                _record_path(conn, key, status, sessions, rows, detail)
            if status == REFUSED:
                report["refused"] += 1
                reason = _reason(detail)
                report["settled"] += settled(VendorRefusal(reason or "", ""), key, prior)
            else:
                report[status] += 1
                report["sessions"] += sessions
                report["rows"] += rows
            rep.advance("paths", current=f"{key[0]} {key[1]}", key=key[0])

        for key in todo:
            if not waiting.get(key):
                settle(key)

        pending, logged = iter(order), 0
        with ThreadPoolExecutor(max(1, workers)) as ex:
            running = {}

            def submit_next() -> bool:
                if stop is not None and stop():
                    report["stopped"] = True
                    return False
                day_key = next(pending, None)
                if day_key is None:
                    return False
                sym, day = day_key
                running[ex.submit(client.eod_chain_day, vendor_root(sym), day)] = day_key
                report["days"]["asked"] += 1
                return True

            for _ in range(max(1, workers)):
                if not submit_next():
                    break
            while running:
                finished, _ = wait(running, return_when=FIRST_COMPLETED)
                for fut in finished:
                    sym, day = day_key = running.pop(fut)
                    try:
                        answer = fut.result()
                    except VendorRefusal as refusal:
                        if refusal.reason == NO_DATA:
                            with conn:
                                _record_day(conn, day_key, EMPTY, [], 0, None)
                            report["days"]["no_data"] += 1
                            result = {k: (NODATA, 0) for k in paths_on[day_key]}
                        else:
                            detail = f"{refusal.reason}: {refusal.detail}"[:300]
                            with conn:
                                _record_day(conn, day_key, REFUSED, [], 0, detail)
                            report["days"]["refused"] += 1
                            result = {k: (REFUSED, detail) for k in paths_on[day_key]}
                        if refusal.reason in FATAL:
                            report["aborted"] = f"{refusal.reason}: {refusal.detail}"[:300]
                            for other in running:
                                other.cancel()
                            running.clear()
                            break
                    except Exception as exc:  # noqa: BLE001 -- one day's failure is that day's
                        detail = f"{type(exc).__name__}: {exc}"[:300]
                        with conn:
                            _record_day(conn, day_key, REFUSED, [], 0, detail)
                        report["days"]["refused"] += 1
                        result = {k: (REFUSED, detail) for k in paths_on[day_key]}
                    else:
                        kept = write_day(conn, sym, day, answer, wanted[day_key])
                        report["days"]["answered"] += 1
                        result = {
                            k: (ANSWERED, kept[k[1]]) if k[1] in kept else (ABSENT, 0)
                            for k in paths_on[day_key]
                        }
                    for key, got in result.items():
                        outcome[key][day] = got
                        waiting[key] -= 1
                        if not waiting[key]:
                            settle(key)
                    if not report.get("aborted"):
                        submit_next()
                n = report["days"]["asked"]
                if n and n % 100 == 0 and n != logged:
                    logged = n
                    rate = n / max(1e-9, time.monotonic() - started)
                    log(f"days {n}/{len(order)} ({rate:.2f}/s); paths {report['complete'] + report['empty']}"
                        f" stored, {report['refused']} refused")  # fmt: skip
                if report.get("aborted"):
                    break
        report["seconds"] = round(time.monotonic() - started, 1)
        if report.get("stopped"):
            rep.note("stopped: resumes from here on the next run")
        rep.finish("failed" if report.get("aborted") else "done")
        return report
    finally:
        conn.close()


def progress(
    settings: dict,
    requests: list[tuple[str, str, str]],
    *,
    now: float | None = None,
    window_seconds: float = 600.0,
) -> dict[str, Any]:
    """How far the store has got through one request list: answered (any status) of total, by
    status, and the answer rate over the last `window_seconds` with the ETA it implies. Counts only,
    never a price. A fetch run holds no state of its own, so this reads the store it writes -- the
    same answer whoever started the run."""
    import time

    now = time.time() if now is None else now
    wanted = set(requests)
    out: dict[str, Any] = {"total": len(wanted), "answered": 0, "by_status": {}, "rate_per_min": None,
                           "eta_minutes": None, "last_answer_age_s": None}  # fmt: skip
    path = db_path(settings)
    if not path.exists() or not wanted:
        return out
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        recent, last = 0, None
        for sym, sess, exp, st, at in conn.execute(
            "SELECT symbol, session, expiration, status, fetched_at FROM eod_chain_requests"
        ):
            if (sym, sess, exp) not in wanted:
                continue
            out["answered"] += 1
            out["by_status"][st] = out["by_status"].get(st, 0) + 1
            if isinstance(at, (int, float)):
                last = at if last is None else max(last, at)
                if at >= now - window_seconds:
                    recent += 1
    finally:
        conn.close()
    if last is not None:
        out["last_answer_age_s"] = round(now - last)
    left = out["total"] - out["answered"]
    if recent:
        rate = recent / (window_seconds / 60)
        out["rate_per_min"] = round(rate, 1)
        out["eta_minutes"] = round(left / rate) if left else 0
    return out


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
