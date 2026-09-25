"""Backfill `settlement_fees` for calendars, pmcc and curve positions (2026-09-25).

    python scripts/positions_backfill_settlement_fees.py            # dry run -- prints, changes nothing
    python scripts/positions_backfill_settlement_fees.py --apply    # adds the column, writes

The three modules keep one accounting: `fees` is the total of every cost a position has incurred,
and `exit_cost` accumulates each traded exit's fee, the cash-settlement charge and the assignment
disposal fee. `settlement_fees` is the part of that which is settlement -- recorded going forward by
each module's `_accumulate_exit_costs(..., settlement=True)`; this recovers it for history with the
functions the modules charge it with:

    engine.settlement_fee(itm_settlements - assigned legs)   the cash-settled ITM legs' $5 events
  + SUM(<prefix>assignments.fees)                           each physical assignment's disposal fee

-- `itm - assigned` is the guard calendars and pmcc have always carried. curve lacked it until
2026-09-24 (995fcbe8), which charged an assigned leg twice; no curve position had been assigned, so
no row carries that. Refuses (and writes nothing to that ledger) where the recovered figure exceeds
the row's recorded `exit_cost`, since the settlement part cannot be larger than the costs it is part
of. Runnable from anywhere; ledgers resolve off `$CHERRYPICK_HOME` (default `~/.cherrypick`), or
`--home`. A dry run opens each ledger read-only and never adds the column.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "packages" / "core"))
MODULES = {"calendars": "dc_", "pmcc": "pmcc_", "curve": "curve_"}
for _m in MODULES:
    sys.path.insert(0, str(_REPO / "packages" / _m / "src"))


def backfill(module: str, ledger: Path, apply: bool) -> dict:
    prefix = MODULES[module]
    dbmod = importlib.import_module(f"cherrypick.{module}.db")
    engine = importlib.import_module(f"cherrypick.{module}.engine")
    if apply:
        conn = dbmod.connect(str(ledger))  # adds the column on a ledger that predates it
    else:
        conn = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cols = {c[1] for c in conn.execute(f"PRAGMA table_info({prefix}positions)")}
    unset = "WHERE p.settlement_fees IS NULL" if "settlement_fees" in cols else ""
    rows = conn.execute(
        f"""SELECT p.position_id, p.exit_cost, COALESCE(p.itm_settlements, 0) AS itm,
                   (SELECT COUNT(*) FROM {prefix}assignments a
                     WHERE a.position_id = p.position_id) AS assigned,
                   (SELECT COALESCE(SUM(a.fees), 0) FROM {prefix}assignments a
                     WHERE a.position_id = p.position_id AND a.status = 'disposed') AS assignment_fees
              FROM {prefix}positions p {unset}"""
    ).fetchall()
    out = {"candidates": len(rows), "set": 0, "zero": 0, "refused": []}
    updates: list[tuple[float, str]] = []
    for r in rows:
        fee = round(
            engine.settlement_fee(max(0, int(r["itm"]) - int(r["assigned"]))) + float(r["assignment_fees"]), 2
        )
        if fee > float(r["exit_cost"] or 0.0) + 0.005:
            out["refused"].append((r["position_id"], fee, r["exit_cost"]))
            continue
        updates.append((fee, r["position_id"]))
        out["zero" if fee == 0 else "set"] += 1
    if apply and not out["refused"]:
        conn.executemany(f"UPDATE {prefix}positions SET settlement_fees = ? WHERE position_id = ?", updates)
        conn.commit()
    out["total"] = round(sum(f for f, _ in updates), 2)
    conn.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="add the column and write (default: dry run)")
    ap.add_argument("--home", help="cherrypick home (default: $CHERRYPICK_HOME or ~/.cherrypick)")
    args = ap.parse_args()
    home = Path(args.home or os.environ.get("CHERRYPICK_HOME") or Path.home() / ".cherrypick")
    status = 0
    for module in MODULES:
        ledger = home / "data" / module / "paper_trades.db"
        if not ledger.exists():
            continue
        res = backfill(module, ledger, args.apply)
        mode = "REFUSED" if res["refused"] else "APPLIED" if args.apply else "DRY RUN"
        print(
            f"{mode} {module}: {res['candidates']} positions without it -- {res['set']} with a charge, "
            f"{res['zero']} at zero; ${res['total']:.2f} in all"
        )
        for pid, fee, cost in res["refused"][:10]:
            print(f"  refused {pid}: recovered {fee} > recorded exit_cost {cost}")
        status = status or (2 if res["refused"] else 0)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
