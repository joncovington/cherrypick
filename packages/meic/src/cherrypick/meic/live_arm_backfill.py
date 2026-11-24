"""Fill `ic_trades.live_arm` on live rows written before the column existed.

A live row's own `arm` is the constant 'live' (the account); the registry arm live ran (`live.arm`,
`pm-200`) is on the trade's FILLED entry attempt, joined by order id. New entries record it on the
row; this copies it onto the older ones. A row with no filled attempt (it predates attempts being
journaled), or whose attempt ran with `live.arm` unset (the attempt's arm is then 'live'), stays NULL.
Only NULL rows are touched, so a second run changes nothing.

Dry run by default: it prints what it would fill. `--apply` backs the ledger up with SQLite's backup
API (beside the ledger, `.bak-pre-live-arm-<stamp>`) and then fills the column in one transaction.
Run it with the live loop stopped.

    python -m cherrypick.meic.live_arm_backfill [--db PATH] [--apply]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from cherrypick.meic import paths as _paths

# The attempt's arm is 'live' when the live loop ran with live.arm unset (live_loop._record_attempt).
_ACCOUNT_TAG = "live"

_FILLED_ARM = (
    "(SELECT a.arm FROM entry_attempts a WHERE a.ic_order_id = ic_trades.ic_order_id "
    "AND a.outcome = 'filled' AND a.arm IS NOT NULL AND a.arm != ? ORDER BY a.id LIMIT 1)"
)


def _has(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def plan(conn: sqlite3.Connection) -> dict:
    """What the backfill would do: the rows to fill (id, order id, arm) and how many stay NULL. Reads
    only. `ready` is False when the ledger has no `live_arm` column yet (start the live loop or run
    `db.py init_db` once) or no attempts to read."""
    if not _has(conn, "ic_trades", "live_arm"):
        return {"ready": False, "reason": "ic_trades has no live_arm column yet", "fill": [], "unmatched": 0}
    if not all(_has(conn, "entry_attempts", c) for c in ("ic_order_id", "arm", "outcome")):
        return {"ready": False, "reason": "no entry_attempts to read", "fill": [], "unmatched": 0}
    rows = conn.execute(
        f"SELECT id, ic_order_id, {_FILLED_ARM} FROM ic_trades WHERE live_arm IS NULL ORDER BY id",
        (_ACCOUNT_TAG,),
    ).fetchall()
    fill = [{"id": r[0], "ic_order_id": r[1], "live_arm": r[2]} for r in rows if r[2] is not None]
    return {"ready": True, "fill": fill, "unmatched": len(rows) - len(fill)}


def apply(db_path: str, stamp: str) -> dict:
    """Back the ledger up, then fill the matched rows. Returns the plan plus the backup path."""
    src = sqlite3.connect(db_path)
    try:
        p = plan(src)
        if not p["ready"] or not p["fill"]:
            return {**p, "applied": False}
        backup = Path(db_path).with_name(f"{Path(db_path).name}.bak-pre-live-arm-{stamp}")
        dst = sqlite3.connect(str(backup))
        try:
            src.backup(dst)
        finally:
            dst.close()
        with src:
            src.executemany(
                "UPDATE ic_trades SET live_arm = ? WHERE id = ? AND live_arm IS NULL",
                [(f["live_arm"], f["id"]) for f in p["fill"]],
            )
        return {**p, "applied": True, "backup": str(backup)}
    finally:
        src.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="the live ledger (default: the module's live ledger)")
    ap.add_argument("--apply", action="store_true", help="write the change (default: print the plan only)")
    args = ap.parse_args(argv)

    db_path = args.db or str(_paths.live_db_path())
    if not Path(db_path).exists():
        print(json.dumps({"ok": False, "error": f"no ledger at {db_path}"}))
        return 1
    if args.apply:
        out = apply(db_path, datetime.now().strftime("%Y%m%d-%H%M%S"))
    else:
        conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        try:
            out = {**plan(conn), "applied": False}
        finally:
            conn.close()
    out["fill_count"] = len(out["fill"])
    print(json.dumps({"ok": True, "db": db_path, **out}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
