"""Correct three rows of the MEIC LIVE ledger (data/meic/meic_trades.db) from 2026-06-25..06-30.

    python scripts/meic_live_ledger_correct.py                       # dry run: the rows, before and after
    python scripts/meic_live_ledger_correct.py --apply               # back up, then fix ids 6 and 7
    python scripts/meic_live_ledger_correct.py --apply --remove-paper-row-1   # also move id 1 out

A recording correction, not a measurement change: it lands whenever it is run. No broker call.

**The convention.** `pnl` on an ic_trades row is GROSS, `(credit - exit) x 100` per closed side, and
net is `pnl - fees` (`db.py`, `core.ledgers` meic_ic). Both live rows below were written by the
agent-era loop, which recorded the order's LIMIT as `net_credit` and never the fill.

- **id 7 (06-30, order 479793516), high confidence.** The entry filled at 0.43, not the 0.32 limit:
  the day's own summary says "$0.43 credit ... closing order filled at $0.39 ... gross +$4.00, fees
  $4.96, net -$0.97", the stop was re-based on 0.43 (current trigger/limit 0.41/0.43), and the account's
  net liquidation value moved -$0.96 that day with no position left. pnl +4.00 and fees 4.96 are
  already right; only `net_credit` 0.32 -> 0.43 is wrong (it made a stop at 0.39 look like a gain).
- **id 6 (06-29, order 479518187), medium-high confidence.** The loop log says "$0.36 limit, filled
  at $0.38 net credit". The call stop (order 479536927) filled at 0.38 net; the put stop was
  cancelled and the put spread closed by hand at 0.02 (no order id recorded): exit 0.40 as stored.
  pnl (0.38 - 0.40) x 100 = -2.00, fees 4.96 (same eight contracts as id 7), net -6.96, which matches
  the account's -$6.967 move between the 06-25 and 06-29 summaries; at 0.36 it would be -8.96.
- **id 1 (06-25, PAPER-20260625-101529-738827).** A paper trade: the loop log says
  live_trading_enabled=false and "Entered W3 paper IC", the id carries the PAPER- prefix, and 46
  paper_marks rows belong to it. No reader checks `is_paper`, so it is counted as a live trade, and
  its pnl 1.44 is NET (gross would be 6.18), so readers net it twice. With --remove-paper-row-1 it is
  exported (row and marks, JSON) to the backup directory and deleted from the live ledger.

What would make id 6 exact: the MEIC account's (****) transaction history for 2026-06-29 -- order
479518187's four opening legs (XSP 260629 P735/P732/C744/C747), order 479536927 (744C/747C), and the
13:31 ET manual put-spread close (735P/732P) -- and for 2026-06-30 orders 479793516 and 479805454.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import time
from pathlib import Path

PAPER_ID = "PAPER-20260625-101529-738827"

# (id, guard: the row as it stands, fix)
FIXES = (
    (
        7,
        {"ic_order_id": "479793516", "net_credit": 0.32, "pnl": 4.0, "fees": 4.96},
        {"net_credit": 0.43},
    ),
    (
        6,
        {"ic_order_id": "479518187", "net_credit": 0.36, "pnl": None, "fees": None},
        {"net_credit": 0.38, "pnl": -2.0, "fees": 4.96, "call_stop_cost": 0.38, "put_stop_cost": 0.02},
    ),
)


def ledger() -> Path:
    home = Path(os.environ.get("CHERRYPICK_HOME") or Path.home() / ".cherrypick")
    return home / "data" / "meic" / "meic_trades.db"


def _row(conn, row_id):
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM ic_trades WHERE id = ?", (row_id,)).fetchone()
    return dict(r) if r else None


def _matches(row: dict, guard: dict) -> bool:
    for key, want in guard.items():
        have = row.get(key)
        if want is None:
            if have is not None:
                return False
        elif isinstance(want, float):
            if have is None or abs(float(have) - want) > 1e-9:
                return False
        elif str(have) != str(want):
            return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--remove-paper-row-1", action="store_true", help="export and delete the paper row id 1")
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)
    db = Path(args.db) if args.db else ledger()
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    plans = []
    for row_id, guard, fix in FIXES:
        row = _row(conn, row_id)
        if row is None:
            print(f"id {row_id}: not found; skipped")
            continue
        if all(row.get(k) == v for k, v in fix.items()):
            print(f"id {row_id}: already corrected")
            continue
        if not _matches(row, guard):
            print(
                f"id {row_id}: does not look as expected ({ {k: row.get(k) for k in guard} }); "
                "refusing to touch it"
            )
            continue
        print(f"id {row_id} ({row['trade_date']}, {row['status']}):")
        for key, value in fix.items():
            print(f"    {key}: {row.get(key)!r} -> {value!r}")
        net_after = (fix.get("pnl", row.get("pnl")) or 0) - (fix.get("fees", row.get("fees")) or 0)
        print(f"    net (pnl - fees) after: {net_after:+.2f}")
        plans.append((row_id, guard, fix))
    paper = _row(conn, 1)
    marks = []
    if paper and paper.get("ic_order_id") == PAPER_ID and int(paper.get("is_paper") or 0) == 1:
        conn.row_factory = sqlite3.Row
        marks = [
            dict(r) for r in conn.execute("SELECT * FROM paper_marks WHERE ic_order_id = ?", (PAPER_ID,))
        ]
        verb = (
            "would be exported and deleted"
            if args.remove_paper_row_1
            else "stays (pass --remove-paper-row-1)"
        )
        print(
            f"id 1 ({PAPER_ID}): a paper trade in the live ledger, "
            f"with {len(marks)} paper_marks rows -- {verb}"
        )
    else:
        paper = None
    conn.close()

    if not args.apply:
        print("\ndry run: nothing written")
        return 0
    if not plans and not (paper and args.remove_paper_row_1):
        print("nothing to do")
        return 0

    dest = db.parent.parent.parent / "backups" / f"meic-live-ledger-correct-{time.strftime('%Y%m%d-%H%M%S')}"
    dest.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(db))
    out = sqlite3.connect(str(dest / db.name))
    src.backup(out)
    out.close()
    print(f"backed up to {dest}")
    now = dt.datetime.now().astimezone().isoformat()
    try:
        with src:
            for row_id, guard, fix in plans:
                sets = ", ".join(f"{k} = ?" for k in fix) + ", updated_at = ?"
                where = " AND ".join(
                    f"{k} IS NULL"
                    if v is None
                    else (f"ABS({k} - ?) < 1e-9" if isinstance(v, float) else f"{k} = ?")
                    for k, v in guard.items()
                )
                params = [*fix.values(), now, row_id, *[v for v in guard.values() if v is not None]]
                cur = src.execute(f"UPDATE ic_trades SET {sets} WHERE id = ? AND {where}", params)
                if cur.rowcount != 1:
                    raise RuntimeError(f"id {row_id}: expected one row updated, got {cur.rowcount}")
                print(f"id {row_id}: corrected")
            if paper and args.remove_paper_row_1:
                (dest / "paper-row-1.json").write_text(
                    json.dumps({"ic_trades": paper, "paper_marks": marks}, indent=2, default=str),
                    encoding="utf-8",
                )
                src.execute("DELETE FROM paper_marks WHERE ic_order_id = ?", (PAPER_ID,))
                cur = src.execute(
                    "DELETE FROM ic_trades WHERE id = 1 AND ic_order_id = ? AND is_paper = 1", (PAPER_ID,)
                )
                if cur.rowcount != 1:
                    raise RuntimeError("id 1: expected one row deleted")
                print(f"id 1: exported to {dest / 'paper-row-1.json'} and deleted")
    finally:
        src.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
