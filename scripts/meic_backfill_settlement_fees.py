"""Backfill MEIC's `settlement_fees` column on rows closed before it was recorded (2026-09-25).

    python scripts/meic_backfill_settlement_fees.py            # dry run -- prints, changes nothing
    python scripts/meic_backfill_settlement_fees.py --apply    # writes

`settlement_fees` is the part of a trade's `fees` that is the cash-settlement charge, $5 per ITM
strike (`paper.expire_fees`). The write path records it from 2026-09-25; this recovers it for the
history, so the suite's trade tables can show trading fees and settlement apart. Nothing else on a
row changes: `fees` stays the total and `pnl` is untouched.

What a row gets depends on what its `fees` total actually CONTAINS, not on what it should have:

  - closed without settling       0 -- stopped on both sides, or force-closed: nothing was left to
                                  settle, so no part of the total is settlement.
  - settled at expiry, paper      `paper.expire_fees(n)`, n = `paper.settlement_itm_strikes` over the
                                  sides still held at the recorded `settle_underlying` -- the count
                                  and fee `scripts/meic_resettle_expiry_fees.py` added to this row's
                                  total. Only on a ledger that run was applied to (its marker file
                                  sits beside the ledger): before it, settlement was never charged,
                                  and a computed fee would not be a part of the total.
  - settled, price not recorded   left NULL. The re-settle skipped these (99 rows) and charged
                                  nothing, but the fee they should have paid is unknown -- "not
                                  recorded", never a guessed zero.
  - settled, no re-settle marker  left NULL, for the same reason (the live ledger's one expired
                                  row, from 2026-06-25).

Refuses (and writes nothing) if any computed fee exceeds the row's recorded total, which would mean
the total was never the modelled one. Runnable from anywhere; the ledgers resolve off
`$CHERRYPICK_HOME` (default `~/.cherrypick`), or `--home`.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "packages" / "core"))
sys.path.insert(0, str(_REPO / "packages" / "meic" / "src"))

from cherrypick.meic import db as dbmod  # noqa: E402
from cherrypick.meic import paper  # noqa: E402

LEDGERS = ("paper_trades.db", "meic_trades.db")


def _resettled(ledger: Path) -> bool:
    return ledger.with_name(f"{ledger.name}.expiry-fee-resettled.json").exists()


def _settles(row: sqlite3.Row) -> bool:
    """A close that reached expiry on at least one side: its total may hold a settlement charge."""
    return "expire" in (row["exit_reason"] or "") or row["status"] == "expired"


def backfill(ledger: Path, apply: bool) -> dict:
    # A dry run opens the ledger read-only and never adds the column; only --apply migrates.
    conn = sqlite3.connect(ledger) if apply else sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    dbmod._refuse_pre_rename(conn)
    has_column = any(c[1] == "settlement_fees" for c in conn.execute("PRAGMA table_info(ic_trades)"))
    if apply and not has_column:
        dbmod._migrate(conn)
        has_column = True
    unset = "settlement_fees IS NULL AND " if has_column else ""
    rows = conn.execute(
        "SELECT ic_order_id, status, exit_reason, put_strike, call_strike, wing_width,"
        " settle_underlying, put_stop_cost, call_stop_cost, fees FROM ic_trades"
        f" WHERE {unset}status NOT IN ('open', 'pending', 'cancelled')"
    ).fetchall()
    resettled = _resettled(ledger)
    out = {"ledger": ledger.name, "candidates": len(rows), "set": 0, "zero": 0, "left_null": 0, "refused": []}
    updates: list[tuple[float, str]] = []
    for r in rows:
        if not _settles(r):
            fee = 0.0
        else:
            n = paper.settlement_itm_strikes(
                r["put_strike"],
                r["call_strike"],
                r["wing_width"],
                r["settle_underlying"],
                put_open=r["put_stop_cost"] is None,
                call_open=r["call_stop_cost"] is None,
            )
            if n is None or not resettled:
                out["left_null"] += 1
                continue
            fee = round(paper.expire_fees(n), 2)
        if fee > (r["fees"] or 0.0) + 0.005:
            out["refused"].append((r["ic_order_id"], fee, r["fees"]))
            continue
        updates.append((fee, r["ic_order_id"]))
        out["zero" if fee == 0 else "set"] += 1
    if apply and not out["refused"]:
        conn.executemany("UPDATE ic_trades SET settlement_fees = ? WHERE ic_order_id = ?", updates)
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
        ledger = home / "data" / "meic" / name
        if not ledger.exists():
            continue
        res = backfill(ledger, args.apply)
        mode = "REFUSED" if res["refused"] else "APPLIED" if args.apply else "DRY RUN"
        print(
            f"{mode} {name}: {res['candidates']} closed rows without it -- {res['set']} with a fee, "
            f"{res['zero']} at zero, {res['left_null']} left NULL; ${res['total']:.2f} in all"
        )
        for oid, fee, total in res["refused"][:10]:
            print(f"  refused {oid}: computed {fee} > recorded fees {total}")
        status = status or (2 if res["refused"] else 0)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
