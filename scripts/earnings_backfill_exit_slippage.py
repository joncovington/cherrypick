"""Remove the double-charged exit slippage from earnings' closed paper rows (to 2026-10-23).

Every earnings exit is priced on the natural side (`scanner.compute_generic_exit_debit`: shorts at
the ask, longs at the bid), which concedes the spread inside gross `pnl`. Until 2026-10-23 the close
paths also charged the core slippage haircut into `exit_cost`, taking the spread twice. The haircut
charged is exactly the row's own `exit_slippage`, so this subtracts that from `exit_cost` and sets
`exit_slippage` to 0 -- what `costs.natural_exit_costs` now records. `pnl` (gross), `exit_debit` and
every entry column are untouched; only the cost side, and so net, moves.

  - exit_slippage > 0          exit_cost -= exit_slippage; exit_slippage = 0
  - exit_slippage NULL         left alone: pre-instrumentation rows (before 2026-07-29) folded an
                               unrecorded haircut into exit_cost, and it cannot be separated out
  - exit_slippage 0            already right (settled-only closes, or already backfilled)

Dry run by default; --apply writes, after copying the ledger beside itself. Refuses a row whose
exit_slippage exceeds its exit_cost, which would mean the cost was never the modelled stack.
Re-running after --apply is a no-op.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from cherrypick.earnings import paths


def backfill(db_path: Path, apply: bool) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT order_id, arm, exit_cost, exit_slippage FROM trades"
        " WHERE closed_at IS NOT NULL AND exit_slippage > 0"
    ).fetchall()
    out = {
        "db": str(db_path),
        "rows": len(rows),
        "removed": 0.0,
        "by_arm": {},
        "left_null": conn.execute(
            "SELECT COUNT(*) FROM trades WHERE closed_at IS NOT NULL AND exit_slippage IS NULL"
        ).fetchone()[0],
        "refused": [],
        "applied": False,
    }
    updates = []
    for r in rows:
        if r["exit_slippage"] > (r["exit_cost"] or 0.0) + 0.005:
            out["refused"].append((r["order_id"], r["exit_slippage"], r["exit_cost"]))
            continue
        updates.append((round(r["exit_cost"] - r["exit_slippage"], 2), r["order_id"]))
        out["removed"] = round(out["removed"] + r["exit_slippage"], 2)
        out["by_arm"][r["arm"]] = round(out["by_arm"].get(r["arm"], 0.0) + r["exit_slippage"], 2)
    if apply and updates and not out["refused"]:
        backup = db_path.with_name(f"{db_path.name}.bak-pre-exit-slippage-{time.strftime('%Y%m%d-%H%M%S')}")
        conn.close()
        shutil.copy2(db_path, backup)
        conn = sqlite3.connect(str(db_path))
        conn.executemany("UPDATE trades SET exit_cost = ?, exit_slippage = 0 WHERE order_id = ?", updates)
        conn.commit()
        out["applied"] = True
        out["backup"] = str(backup)
    conn.close()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--db", type=Path, default=None, help="ledger (default: the paper ledger)")
    args = ap.parse_args(argv)
    db = args.db or Path(paths.data_path("paper_trades.db"))
    out = backfill(db, args.apply)
    for k, v in out.items():
        print(f"{k}: {v}")
    return 1 if out["refused"] else 0


if __name__ == "__main__":
    sys.exit(main())
