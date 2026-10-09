"""Pull the DoltHub datasets the earnings module reads, and record how far its calendar now reaches.

**Why this exists.** On 2026-08-25 the earnings module was found not to have paper traded for eleven
sessions. Nothing was broken: Dolt was running, its tables were fully populated, the loop ticked and
the config was enabled. The local clone was simply 55 commits behind, so `earnings_calendar` ended
at 2026-08-14 and the scanner had no upcoming announcements to scan. An earnings calendar published
on a given day only reaches ~5 weeks forward, so a clone that is never pulled stops feeding the
module roughly a month later — silently, because "no candidates today" and "a quiet earnings week"
look identical.

Nothing in the suite refreshed it. The `earnings-dolt` job keeps the sql-server alive; it never
updated the data.

A script rather than package code, for the same reason as the narratives and the futures resolver:
this reaches the network, and nothing on a decision path may. It is read-only against the suite —
it writes only the Dolt clones it owns and one state file — and a failure leaves the previous data
exactly where it was.

The state file is what makes the staleness VISIBLE: `state/dolt_data.json` records the calendar's
furthest date, and the watchdog reads that file (it is stdlib-and-files only, so it cannot query
Dolt itself) to warn before the horizon runs out rather than after.

After the pulls it compacts any clone whose loose storage has grown past a threshold (see
`compact_loose`): each pull leaves uncompressed table files behind, and on 2026-10-01 the first
compaction took `stocks` from 4.71 GB to 3.02 GB and `earnings` from 1.83 GB to 1.35 GB with the
data unchanged.

    python scripts/refresh_dolt_data.py [--dry-run]
    python scripts/refresh_dolt_data.py --recheck   # re-read the calendar only
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from cherrypick.core import home as _home

STATE_NAME = "dolt_data.json"
# The clones the earnings module reads. `earnings` carries the announcement calendar that decides
# whether the scanner has anything to look at; the other two carry the price/option history it
# scores candidates with.
DATABASES = ("earnings", "options", "stocks")


def _data_dir() -> Path:
    return _home.data_dir("earnings")


def _pull_via_server(name: str) -> str:
    """`CALL DOLT_PULL('origin')` on the running sql-server; its one-line message.

    Through the server, not the CLI, because the server holds the clone's storage lock. Dolt's CLI
    reaches a running server only through a `sql-server.info` file in the data directory; on
    2026-10-09 the server the Windows service had started after a reboot left none, so every
    `dolt pull` opened the clone read-only and failed ("cannot update manifest: database is read
    only") with nothing on its output, while the same pull through the server landed."""
    import mysql.connector as _mysql

    cn = _mysql.connect(host="127.0.0.1", port=3306, user="root", database=name, connection_timeout=30)
    try:
        cur = cn.cursor()
        cur.execute("CALL DOLT_PULL('origin')")
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description or ()]
    finally:
        cn.close()
    row = dict(zip(cols, rows[0], strict=False)) if rows else {}
    return str(row.get("message") or "pulled")


def _server_unreachable(exc: Exception) -> bool:
    """A connection that never opened -- the one case the CLI pull is the right fallback for. A
    pull the server ran and refused is the answer, not a reason to try again another way."""
    return isinstance(exc, ImportError) or getattr(exc, "errno", None) in _CANNOT_CONNECT


# MySQL client codes for a connection that never opened: no socket (2002), refused (2003), unknown host
# (2005). mysql-connector raises a refused connection as a DatabaseError, so the code is what counts.
_CANNOT_CONNECT = frozenset({2002, 2003, 2005})


def _pull(repo: Path, via_server=_pull_via_server) -> dict:
    if not (repo / ".dolt").is_dir():
        return {"ok": False, "reason": "not_a_dolt_clone"}
    try:
        message = via_server(repo.name)
        return {"ok": True, "via": "server", "tail": [message]}
    except Exception as exc:  # noqa: BLE001 -- recorded below; a failed pull leaves the data where it was
        if not _server_unreachable(exc):
            return {"ok": False, "via": "server", "reason": f"{type(exc).__name__}: {exc}"[:300]}
    return _pull_cli(repo)


def _pull_cli(repo: Path) -> dict:
    """`dolt pull` in the clone: only when no server is listening, so nothing holds its lock."""
    try:
        res = subprocess.run(["dolt", "pull"], cwd=repo, capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    # Dolt draws an ANSI progress spinner on the same line; keeping it would bury the one line that
    # matters ("Everything up-to-date", or the commit range it moved) under kilobytes of backspaces.
    raw = (res.stdout or "") + (res.stderr or "")
    lines = []
    for chunk in raw.replace(chr(13), chr(10)).splitlines():
        seg = chunk.split(chr(8))[-1].strip()
        if seg and "Pulling..." not in seg and "Fetching..." not in seg:
            lines.append(seg)
    return {
        "ok": res.returncode == 0,
        "via": "cli",
        "returncode": res.returncode,
        "tail": lines[-3:],
    }


# How many times, and how far apart, the calendar is read after a pull. The read runs through the
# sql-server the pull has just rewritten the clone under, and on 2026-09-27 it failed once right
# after a successful pull while the same query answered fine minutes later -- and the null it wrote
# sat in the state file until the next day's pull, so the watchdog warned every hour about a
# calendar that was perfectly readable.
READ_ATTEMPTS = 4
READ_PAUSE_S = 15.0


def _read_calendar_once() -> str | None:
    import mysql.connector as _mysql

    cn = _mysql.connect(host="127.0.0.1", port=3306, user="root", database="earnings", connection_timeout=15)
    try:
        cur = cn.cursor()
        cur.execute("SELECT MAX(date) FROM earnings_calendar")
        row = cur.fetchone()
        return str(row[0]) if row and row[0] is not None else None
    finally:
        cn.close()


def calendar_max_date(
    read=_read_calendar_once, attempts: int = READ_ATTEMPTS, pause=None
) -> tuple[str | None, str | None]:
    """(how far the announcement calendar reaches, why it could not be read). Retried, because the
    first read after a pull can land while the server is still settling. The error is kept rather
    than swallowed: the watchdog quotes it, so a warning says what failed instead of only that
    something did."""
    pause = pause if pause is not None else (lambda: time.sleep(READ_PAUSE_S))
    error = None
    for attempt in range(attempts):
        if attempt:
            pause()
        try:
            value = read()
        except Exception as exc:  # noqa: BLE001 -- the pull still succeeded; only the reading failed
            error = f"{type(exc).__name__}: {exc}"
            continue
        if value is not None:
            return value, None
        error = "earnings_calendar is empty"
    return None, error


# Compaction. Dolt keeps a clone's history in compressed archives (`*.darc`); each pull adds
# uncompressed table files beside them, plus its write journal, and nothing reclaims either until
# a garbage collection rewrites them. Compacting only past a threshold makes it free on the days
# there is nothing to reclaim, and impossible to fall behind a run of large pulls.
COMPACT_LOOSE_BYTES = 500 * 1024 * 1024


def loose_bytes(repo: Path) -> int:
    """Bytes in a clone's storage that are not yet compacted: the 32-character, extensionless table
    files and journal Dolt writes under `.dolt/noms`. Archives, the manifest and the lock are not."""
    noms = repo / ".dolt" / "noms"
    if not noms.is_dir():
        return 0
    total = 0
    for f in noms.rglob("*"):
        if f.is_file() and len(f.name) == 32 and "." not in f.name:
            total += f.stat().st_size
    return total


def _storage_bytes(repo: Path) -> int:
    return sum(f.stat().st_size for f in (repo / ".dolt").rglob("*") if f.is_file())


def _gc_via_server(name: str) -> None:
    """`CALL DOLT_GC()` on the running sql-server: the documented way to compact a clone a server is
    serving. It rewrites storage, never data; the scanner reads the same rows before and after."""
    import mysql.connector as _mysql

    cn = _mysql.connect(host="127.0.0.1", port=3306, user="root", database=name, connection_timeout=30)
    try:
        cur = cn.cursor()
        cur.execute("CALL DOLT_GC()")
        cur.fetchall()
    finally:
        cn.close()


def compact_loose(base: Path, names, threshold: int = COMPACT_LOOSE_BYTES, gc=_gc_via_server) -> dict:
    """Compact each named clone whose loose storage exceeds `threshold`. Per clone: what was loose,
    and when compacted, the storage before and after, how long it took, or why it failed. A failed
    compaction leaves the clone as it was and is recorded, never raised: the pull already landed."""
    out = {}
    for name in names:
        repo = base / name
        loose = loose_bytes(repo)
        rec = {"loose_bytes": loose, "compacted": False}
        if loose > threshold:
            before = _storage_bytes(repo)
            t0 = time.monotonic()
            try:
                gc(name)
                rec.update({"compacted": True, "before_bytes": before, "after_bytes": _storage_bytes(repo)})
            except Exception as exc:  # noqa: BLE001 -- recorded; the data and the pull are unaffected
                rec["error"] = f"{type(exc).__name__}: {exc}"
            rec["seconds"] = round(time.monotonic() - t0, 1)
        out[name] = rec
    return out


def _write_state(payload: dict) -> None:
    path = _home.state_dir() / STATE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def recheck() -> dict:
    """Re-read the calendar into the existing state file without pulling -- how a read that failed
    transiently is cleared the same day instead of warning until the next pull."""
    path = _home.state_dir() / STATE_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    max_date, error = calendar_max_date()
    payload.update(
        {
            "earnings_calendar_max_date": max_date,
            "earnings_calendar_error": error,
            "earnings_calendar_checked_at": datetime.now(UTC).isoformat(),
        }
    )
    _write_state(payload)
    return payload


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report state, pull nothing")
    ap.add_argument(
        "--recheck", action="store_true", help="re-read the calendar into the state file; pull nothing"
    )
    args = ap.parse_args(argv)
    if args.recheck:
        payload = recheck()
        print(json.dumps(payload, indent=2))
        return 0 if payload.get("earnings_calendar_max_date") else 1

    base = _data_dir()
    results = {}
    if not args.dry_run:
        for name in DATABASES:
            results[name] = _pull(base / name)

    max_date, error = calendar_max_date()
    # After the calendar read, so a compaction can never delay the number the watchdog acts on; and
    # only for clones that pulled cleanly -- a failed pull is the thing to look at, not its storage.
    compaction = {} if args.dry_run else compact_loose(base, [n for n, r in results.items() if r.get("ok")])
    payload = {
        "refreshed_at": datetime.now(UTC).isoformat(),
        "databases": results,
        "compaction": compaction,
        # The one number the watchdog acts on: past this date the scanner has nothing to scan.
        "earnings_calendar_max_date": max_date,
        "earnings_calendar_error": error,
        "earnings_calendar_checked_at": datetime.now(UTC).isoformat(),
    }
    if not args.dry_run:
        _write_state(payload)
    print(json.dumps(payload, indent=2))
    return 0 if all(r.get("ok") for r in results.values()) or args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
