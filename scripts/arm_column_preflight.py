"""Read-only preflight for the arm-column migration (Phase 5). Changes nothing, ever.

Reports exactly what a run would do: every ledger under the suite's data dir that carries an arm
column under any of its four names, the rows and indexes each rename would move, and whether the
file is already migrated. Run it before and after; the "after" pass should report nothing pending.

Deliberately driven off the FILESYSTEM rather than a table of modules. The migration plan's own
inventory was hand-written and listed seven paper ledgers -- it missed four LIVE ones (bwb, flies,
meic, earnings) carrying the same columns, read by the same code, plus three practice/backup files.
A hand-kept list is how that happens; walking the directory is how it stops happening.

    python scripts/arm_column_preflight.py [--json]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# The four spellings, and the one they all become.
OLD_NAMES = ("book", "risk_profile", "profile")
CANONICAL = "arm"


def _data_dir() -> Path:
    import os

    home = os.environ.get("CHERRYPICK_HOME")
    return (Path(home) if home else Path.home() / ".cherrypick") / "data"


def _kind(path: Path) -> str:
    n = path.name
    if "live_trades" in n or n in ("meic_trades.db", "earnings_trades.db"):
        return "live"
    if "bak" in n or "pre-reset" in n or "pre-settlement" in n or "practice" in n:
        return "backup"
    return "paper"


def scan(root: Path) -> list[dict]:
    out: list[dict] = []
    for db in sorted(root.rglob("*.db")):
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        except sqlite3.Error:
            continue  # not a database, or locked -- a preflight never blocks on one
        for table in tables:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            old = [c for c in OLD_NAMES if c in cols]
            if not old and CANONICAL not in cols:
                continue
            indexes = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                    (table,),
                )
            ]
            out.append(
                {
                    "db": str(db),
                    "module": db.parent.name,
                    "file": db.name,
                    "kind": _kind(db),
                    "table": table,
                    "from": old[0] if old else None,
                    "done": CANONICAL in cols and not old,
                    "half": bool(old) and CANONICAL in cols,
                    "rows": conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                    "indexes": indexes,
                }
            )
        conn.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = scan(_data_dir())
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    pending = [r for r in rows if not r["done"]]
    half = [r for r in rows if r["half"]]
    print(f"arm-column preflight over {_data_dir()}\n")
    for kind in ("live", "paper", "backup"):
        group = [r for r in rows if r["kind"] == kind]
        if not group:
            continue
        print(f"[{kind}]")
        for r in sorted(group, key=lambda x: (x["module"], x["file"], x["table"])):
            state = "done" if r["done"] else ("HALF-MIGRATED" if r["half"] else f"{r['from']} -> {CANONICAL}")
            idx = f"+{len(r['indexes'])} idx" if r["indexes"] else ""
            where = f"{r['module']}/{r['file']}"
            print(f"  {where:<52s} {r['table']:<22s} {r['rows']:>7,} rows  {idx:<7s} {state}")
        print()

    moving = sum(r["rows"] for r in pending)
    print(f"{len(rows)} arm-bearing tables, {len(pending)} pending, {moving:,} rows to move")
    if half:
        print(f"REFUSE: {len(half)} table(s) carry BOTH names -- resolve before running the migration")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
