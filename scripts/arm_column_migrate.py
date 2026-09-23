"""Apply the arm-column rename (Phase 5). The companion to `arm_column_preflight.py`.

    python scripts/arm_column_migrate.py            # dry run -- prints, changes nothing
    python scripts/arm_column_migrate.py --apply    # renames

Runnable from anywhere: paths resolve off this file, not the working directory.

**The code and the columns have to move together.** A ledger renamed while the code still reads the
old name is a module that cannot see its own positions. Run this against a checkout that expects
the new name (for the four `book` modules, that is the `suite-arm-columns` work), and back the
ledgers up first -- this is the one step in the arm migration that touches data.

The rename itself is `core.db.rename_column`, which carries indexes and constraints across (see
that function for why it is a RENAME rather than the rebuild the suite's other migration precedent
uses) and refuses a table carrying both names rather than guessing which holds the rows.

`--only` is the safety scope and defaults to `book` on purpose. The four `book` modules (bwb,
calendars, curve, pmcc) share `core.ledgerstore.record_decision` and cannot move one at a time;
meic's `risk_profile` and earnings' `profile` are independent of them and of each other, so they
move in their own windows rather than widening this one.

Practice and backup ledgers are skipped unless `--include-backups` is passed. `practice_trades.db`
is read by meic's practice path, so it is not inert -- but it rides meic's window, not this one.

Verify with the preflight afterwards: a completed run leaves nothing pending.
"""

from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[1]
sys.path.insert(0, str(_REPO / "packages" / "core"))

from cherrypick.core import db as _db  # noqa: E402


def _preflight():
    """The preflight module, loaded by path so the two stay a pair and the inventory is discovered
    exactly once, by the same walk, rather than described twice."""
    spec = importlib.util.spec_from_file_location(
        "arm_column_preflight", _HERE.parent / "arm_column_preflight.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="actually rename (default: dry run)")
    ap.add_argument(
        "--only",
        default="book",
        help="comma-separated source column names to migrate (default: book -- the four-module unit)",
    )
    ap.add_argument(
        "--include-backups", action="store_true", help="also migrate practice/backup ledgers"
    )
    args = ap.parse_args()

    pf = _preflight()
    found = pf.scan(pf._data_dir())

    half = [r for r in found if r["half"]]
    if half:
        for r in half:
            print(f"REFUSE {r['module']}/{r['file']}::{r['table']} carries BOTH names")
        print("\nResolve the half-migrated tables before running this.")
        return 2

    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    rows = [r for r in found if not r["done"] and r["from"] in wanted]
    if not args.include_backups:
        rows = [r for r in rows if r["kind"] != "backup"]

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"{len(rows)} tables to rename from {sorted(wanted)} ({mode})\n")
    renamed = 0
    for r in sorted(rows, key=lambda x: (x["module"], x["file"], x["table"])):
        tag = f"{r['module']}/{r['file']}::{r['table']}"
        if not args.apply:
            print(
                f"  would rename {tag}  {r['from']} -> arm"
                f"  ({r['rows']:,} rows, {len(r['indexes'])} idx)"
            )
            continue
        conn = sqlite3.connect(r["db"])
        try:
            changed = _db.rename_column(conn, r["table"], r["from"], "arm")
        finally:
            conn.close()
        print(f"  {'renamed' if changed else 'no-op  '} {tag}  ({r['rows']:,} rows)")
        renamed += changed

    print(f"\n{renamed} renamed" if args.apply else "\n(dry run -- pass --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
