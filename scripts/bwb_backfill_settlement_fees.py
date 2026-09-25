"""Backfill bwb's `settlement_fees` column on positions closed before it was recorded (2026-09-25).

    python scripts/bwb_backfill_settlement_fees.py            # dry run -- prints, changes nothing
    python scripts/bwb_backfill_settlement_fees.py --apply    # writes

`settlement_fees` is the part of a position's `fees` that is the cash-settlement charge, $5 per
distinct ITM symbol (`engine.settlement_fee`). bwb has always charged it and always counted the ITM
symbols (`itm_settlements`); it never recorded the fee on its own. This recovers it with the SAME
function settlement uses, from the row's own count, and writes it only where the row's recorded
parts add up to its total:

    fees == entry_cost + entry_slippage + addon_cost + addon_slippage + settlement_fees

-- the modelled total bwb has always written. A row where that fails is refused and the run writes
nothing, because a settlement figure that is not a part of the recorded total is not the column's
meaning. A broker-reconciled live row is left NULL: its `fees` is the broker's own total, and
`fee_reconcile` now records the broker's settlement part at reconciliation.

Runnable from anywhere; the ledgers resolve off `$CHERRYPICK_HOME` (default `~/.cherrypick`), or
`--home`. A dry run opens each ledger read-only and never adds the column.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "packages" / "core"))
sys.path.insert(0, str(_REPO / "packages" / "bwb" / "src"))

from cherrypick.bwb import db as dbmod  # noqa: E402
from cherrypick.bwb import engine  # noqa: E402

LEDGERS = ("paper_trades.db", "live_trades.db")
PARTS = ("entry_cost", "entry_slippage", "addon_cost", "addon_slippage")


def backfill(ledger: Path, apply: bool) -> dict:
    if apply:
        conn = dbmod.connect(str(ledger))  # adds the column on a ledger that predates it
    else:
        conn = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cols = {c[1] for c in conn.execute("PRAGMA table_info(bwb_positions)")}
    unset = "settlement_fees IS NULL AND " if "settlement_fees" in cols else ""
    source = "fees_source" if "fees_source" in cols else "NULL AS fees_source"
    rows = conn.execute(
        f"SELECT position_id, fees, itm_settlements, {source}, {', '.join(PARTS)}"
        f" FROM bwb_positions WHERE {unset}status = 'closed'"
    ).fetchall()
    out = {"ledger": ledger.name, "candidates": len(rows), "set": 0, "zero": 0, "left_null": 0, "refused": []}
    updates: list[tuple[float, str]] = []
    for r in rows:
        if r["fees_source"] == "reconciled":
            out["left_null"] += 1
            continue
        fee = round(engine.settlement_fee(int(r["itm_settlements"] or 0)), 2)
        parts = sum(float(r[p] or 0.0) for p in PARTS) + fee
        if abs(parts - float(r["fees"] or 0.0)) > 0.011:
            out["refused"].append((r["position_id"], round(parts, 2), r["fees"]))
            continue
        updates.append((fee, r["position_id"]))
        out["zero" if fee == 0 else "set"] += 1
    if apply and not out["refused"]:
        conn.executemany("UPDATE bwb_positions SET settlement_fees = ? WHERE position_id = ?", updates)
        conn.commit()
    out["total"] = round(sum(f for f, _ in updates), 2)
    conn.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--home", help="cherrypick home (default: $CHERRYPICK_HOME or ~/.cherrypick)")
    args = ap.parse_args()
    home = Path(args.home or os.environ.get("CHERRYPICK_HOME") or Path.home() / ".cherrypick")
    status = 0
    for name in LEDGERS:
        ledger = home / "data" / "bwb" / name
        if not ledger.exists():
            continue
        res = backfill(ledger, args.apply)
        mode = "REFUSED" if res["refused"] else "APPLIED" if args.apply else "DRY RUN"
        print(
            f"{mode} {name}: {res['candidates']} closed positions without it -- {res['set']} with a fee, "
            f"{res['zero']} at zero, {res['left_null']} reconciled left NULL; ${res['total']:.2f} in all"
        )
        for pid, parts, total in res["refused"][:10]:
            print(f"  refused {pid}: parts sum to {parts}, recorded fees {total}")
        status = status or (2 if res["refused"] else 0)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
