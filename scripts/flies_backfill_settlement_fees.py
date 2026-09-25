"""Backfill flies' `settlement_fees` column on rows settled before it was recorded (2026-09-24).

`settlement_fees` is the part of a position's `fees` that is the exercise/assignment charge at
settlement. Flies has always charged it -- `engine.settle` folds `fly.expire_fee(itm legs)` into
`fees` -- but never recorded the part on its own, so the standard trade table could not show fees
and settlement apart. This recomputes it for each settled row with the SAME function settlement
uses, from the row's own strikes and settlement print, so the backfilled value is exactly what was
charged. Nothing else on the row changes: `fees` stays the total, `pnl` is untouched.

  - settled at expiry            fly.assignment_fee(position, settlement_price)
  - closed before expiry         0 (nothing left to settle)
  - live, broker-reconciled      left NULL: `fees` there is the broker's real total, and its
                                 settlement part was not stored -- a modelled figure would not be
                                 a part of that total. Re-running fee_reconcile records the real one.

Dry run by default; --apply writes. Refuses a row whose computed fee exceeds its recorded total,
which would mean the row's fees were never the modelled ones. Paths resolve off the file.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "packages" / "core"))
sys.path.insert(0, str(_REPO / "packages" / "flies" / "src"))

from cherrypick.flies import book as bookmod  # noqa: E402
from cherrypick.flies import db as dbmod  # noqa: E402
from cherrypick.flies import fly  # noqa: E402


def backfill(db_path: Path, apply: bool) -> dict:
    conn = dbmod.connect(str(db_path))  # adds the column on a ledger that predates it
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM fly_positions WHERE status = 'settled' AND settlement_fees IS NULL"
        " AND void_reason IS NULL"
    ).fetchall()
    out = {"db": str(db_path), "candidates": len(rows), "set": 0, "zero": 0, "left_null": 0, "refused": []}
    updates = []
    for r in rows:
        if r["broker_reconciliation_status"] == "reconciled":
            out["left_null"] += 1
            continue
        if r["closed_before_expiry"] or r["settlement_price"] is None:
            fee = 0.0
        else:
            fee = round(fly.assignment_fee(bookmod._to_position(dict(r)), r["settlement_price"]), 2)
        if fee > (r["fees"] or 0.0) + 0.005:
            out["refused"].append((r["position_id"], fee, r["fees"]))
            continue
        updates.append((fee, r["position_id"]))
        out["zero" if fee == 0 else "set"] += 1
    if apply and not out["refused"]:
        conn.executemany("UPDATE fly_positions SET settlement_fees = ? WHERE position_id = ?", updates)
        conn.commit()
    out["total_settlement_fees"] = round(sum(f for f, _ in updates), 2)
    conn.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--home", help="cherrypick home (default: ~/.cherrypick or $CHERRYPICK_HOME)")
    args = ap.parse_args()
    import os

    home = Path(args.home or os.environ.get("CHERRYPICK_HOME") or Path.home() / ".cherrypick")
    status = 0
    for name in ("paper_trades.db", "live_trades.db"):
        db = home / "data" / "flies" / name
        if not db.exists():
            continue
        res = backfill(db, args.apply)
        mode = "APPLIED" if args.apply and not res["refused"] else "DRY RUN" if not args.apply else "REFUSED"
        print(
            f"{mode} {name}: {res['candidates']} settled rows without it -- {res['set']} with a fee, "
            f"{res['zero']} at zero, {res['left_null']} reconciled left NULL; "
            f"${res['total_settlement_fees']:.2f} in all"
        )
        for pid, fee, total in res["refused"][:10]:
            print(f"  refused {pid}: computed {fee} > recorded fees {total}")
        status = status or (2 if res["refused"] else 0)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
