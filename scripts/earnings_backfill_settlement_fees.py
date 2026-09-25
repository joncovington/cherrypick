"""Backfill earnings' `settlement_fees` on paper trades closed before it was recorded (2026-09-25).

    python scripts/earnings_backfill_settlement_fees.py            # dry run -- prints, changes nothing
    python scripts/earnings_backfill_settlement_fees.py --apply    # adds the column, writes

`settlement_fees` is the part of a trade's `exit_cost` that is the exercise/assignment charge on
legs that settled rather than traded out ($5 per ITM strike, `settlement.settlement_fee`). What a
row gets depends on what its `exit_cost` actually contains:

  - closed by a trade             0 -- every leg was bought or sold back, so no part of exit_cost
                                  is settlement.
  - closed by settlement          left to `scripts/earnings_resettle_expiry_fees.py`, which computes
    (`expired`, `front_expiry`)   the fee at each expiration's own settlement close and, on the run
                                  that re-costs exit_cost, records it here in the same transaction.
                                  Without that run the exit_cost still holds the old closing stack,
                                  and no settlement fee is a part of it, so this leaves them NULL.

Order on a ledger that predates the column: run this with --apply first (it runs earnings' own
migration, which adds the column), then the re-settle with --apply. Paper only: the live ledger has
recorded no trades. Runnable from anywhere; the ledger resolves off `$CHERRYPICK_HOME` (default
`~/.cherrypick`), or `--home`. A dry run opens the ledger read-only and never adds the column.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "packages" / "core"))
sys.path.insert(0, str(_REPO / "packages" / "earnings" / "src"))

from cherrypick.earnings import db_paper  # noqa: E402

SETTLED = ("expired", "front_expiry")


def backfill(ledger: Path, apply: bool) -> dict:
    if apply:
        db_paper.DB_PATH = ledger
        conn = db_paper._conn()  # runs the migration that adds the column
    else:
        conn = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    has_column = any(c[1] == "settlement_fees" for c in conn.execute("PRAGMA table_info(trades)"))
    unset = "settlement_fees IS NULL AND " if has_column else ""
    rows = conn.execute(
        f"SELECT order_id, exit_reason FROM trades WHERE {unset}closed_at IS NOT NULL"
    ).fetchall()
    zero = [r["order_id"] for r in rows if r["exit_reason"] not in SETTLED]
    settled = len(rows) - len(zero)
    if apply:
        conn.executemany("UPDATE trades SET settlement_fees = 0 WHERE order_id = ?", [(oid,) for oid in zero])
        conn.commit()
    conn.close()
    return {"candidates": len(rows), "zero": len(zero), "settled": settled}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="add the column and write (default: dry run)")
    ap.add_argument("--home", help="cherrypick home (default: $CHERRYPICK_HOME or ~/.cherrypick)")
    args = ap.parse_args()
    home = Path(args.home or os.environ.get("CHERRYPICK_HOME") or Path.home() / ".cherrypick")
    ledger = home / "data" / "earnings" / "paper_trades.db"
    if not ledger.exists():
        print(f"no ledger at {ledger}")
        return 1
    res = backfill(ledger, args.apply)
    mode = "APPLIED" if args.apply else "DRY RUN"
    print(
        f"{mode} {ledger.name}: {res['candidates']} closed trades without it -- "
        f"{res['zero']} closed by a trade at zero; "
        f"{res['settled']} closed by settlement, left to the re-settle"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
