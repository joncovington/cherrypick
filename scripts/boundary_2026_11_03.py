"""The 2026-11-03 measurement boundary: supervised processes stop being power-throttled.

    python scripts/boundary_2026_11_03.py            # dry run: the breaks it would write
    python scripts/boundary_2026_11_03.py --apply    # back up each ledger, then journal the breaks

Declared 2026-11-02, applied that evening, effective from the 2026-11-03 session. Run it from the live
checkout with the orchestrator change merged (it refuses otherwise).

What changed. The supervisor runs as a Windows service with nobody logged on, so it and every job it
starts were windowless background processes, which Windows power-throttles (EcoQoS). On this host's
hybrid CPU the same CPU-bound loop took 0.36 s normally and 2.45 s with EcoQoS forced on (6.8x), and
the console's reads ran 3-7x faster once it was opted out (#807). From this boundary the supervisor,
every job it spawns and the streamer the watchdog may restart are opted out at start
(`util.never_throttle`; each job's `power_throttling` in supervisor-jobs.json says how it came out).
Every loop's tick takes less wall time from here, so tick timing, quote age at decision and
fill-to-stop latency on either side of this date are not poolable. That is a measurement break for
every module the supervisor runs, paper and live.

The change takes effect for a process when it next starts: paper ticks restart every interval, but a
resident job adopted by a restarted supervisor keeps its old state until it is next spawned, so after
the supervisor restarts, restart the residents too (`run.py restart <job>`; the console opts itself out
already, #807).

This script only journals: one row per ledger, dated 2026-11-03, in that ledger's own schema
(`key`: bwb, calendars, contango, curve, earnings, pmcc; `scope`: meic, flies), found by asking each
module ledger whether it has a `measurement_breaks` table rather than from a hand-kept list. Each
ledger is copied first with SQLite's backup API to ~/.cherrypick/backups/boundary-2026-11-03-<stamp>/.
Idempotent: a rerun updates the same rows. Nothing here talks to a broker.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import sys
import time
from pathlib import Path

BOUNDARY = "2026-11-03"
EARLIEST_APPLY = "2026-11-02"
LEDGER_NAMES = ("paper_trades.db", "live_trades.db", "meic_trades.db", "earnings_trades.db")
WHAT = (
    "supervised processes no longer power-throttled (Windows EcoQoS): the supervisor, every job it "
    "spawns and the watchdog's streamer restart are opted out at start (orchestrator util.never_throttle)"
)
WHY = (
    "as a service child every loop was a throttled background process; on this host CPU-bound work ran "
    "6.8x slower throttled. Tick timing, quote age at decision and stop latency either side are not "
    "poolable. Declared and applied 2026-11-02 (console #807; orchestrator change)"
)


def home() -> Path:
    return Path(os.environ.get("CHERRYPICK_HOME") or Path.home() / ".cherrypick")


def ledgers(h: Path) -> list[tuple[Path, str]]:
    """Every module ledger with a measurement_breaks table, and which schema that table has."""
    out = []
    for db in sorted((h / "data").glob("*/*.db")):
        if db.name not in LEDGER_NAMES:
            continue  # backups and side stores
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(measurement_breaks)")}
        finally:
            conn.close()
        if {"scope", "kind", "reason"} <= cols:
            out.append((db, "scope"))
        elif {"key", "old_value", "new_value"} <= cols:
            out.append((db, "key"))
    return out


def code_present() -> str | None:
    """Why the merged code is missing, or None when this checkout has it."""
    try:
        from cherrypick.orchestrator import supervisor, util
    except ImportError as exc:
        return f"cannot import the orchestrator ({exc})"
    if not hasattr(util, "never_throttle") or "never_throttle" not in vars(supervisor):
        return "this checkout's supervisor does not opt its jobs out; merge the orchestrator change first"
    return None


def write(db: Path, schema: str) -> None:
    conn = sqlite3.connect(str(db))
    try:
        if schema == "scope":
            conn.execute(
                "INSERT INTO measurement_breaks (break_date, scope, kind, reason, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(break_date, scope, kind) DO UPDATE SET "
                "reason = excluded.reason, detail = excluded.detail",
                (
                    BOUNDARY,
                    "*",
                    "process_throttling",
                    WHAT,
                    WHY,
                    dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                ),
            )
        else:
            conn.execute(
                "INSERT INTO measurement_breaks (break_date, key, old_value, new_value, note, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(break_date, key) DO UPDATE SET "
                "old_value = excluded.old_value, new_value = excluded.new_value, note = excluded.note",
                (
                    BOUNDARY,
                    "process_throttling",
                    "Windows power throttling (EcoQoS) as a service background process",
                    "opted out: never throttled",
                    f"{WHAT}. {WHY}",
                    time.time(),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="journal the breaks (default: dry run)")
    args = ap.parse_args(argv)
    h = home()
    today = dt.date.today().isoformat()
    print(f"boundary {BOUNDARY}; home {h}; today {today}; {'APPLY' if args.apply else 'dry run'}")
    found = ledgers(h)
    for db, schema in found:
        print(f"  break {db.parent.name}/{db.name} ({schema} schema): {BOUNDARY} process_throttling")
    missing = code_present()
    if missing:
        print(f"\nREFUSED: {missing}")
    if not args.apply:
        print("\ndry run: nothing written. Re-run with --apply.")
        return 1 if missing else 0
    if missing:
        return 1
    if today < EARLIEST_APPLY:
        print(f"\nrefusing to apply before {EARLIEST_APPLY}")
        return 1
    dest = h / "backups" / f"boundary-{BOUNDARY}-{time.strftime('%Y%m%d-%H%M%S')}"
    dest.mkdir(parents=True, exist_ok=True)
    for db, _ in found:
        src = sqlite3.connect(str(db))
        out = sqlite3.connect(str(dest / f"{db.parent.name}-{db.name}"))
        try:
            src.backup(out)
        finally:
            out.close()
            src.close()
    print(f"backed up to {dest}")
    for db, schema in found:
        write(db, schema)
        print(f"break written: {db.parent.name}/{db.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
