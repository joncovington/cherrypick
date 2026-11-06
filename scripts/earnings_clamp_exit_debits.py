"""Re-price earnings paper exits recorded outside their structure's payoff range (2026-11-05).

    python scripts/earnings_clamp_exit_debits.py            # dry run -- prints, changes nothing
    python scripts/earnings_clamp_exit_debits.py --apply    # backs up the ledger, then corrects it

Runnable from anywhere; the ledger resolves off `$CHERRYPICK_HOME` (default `~/.cherrypick`).

**What was wrong.** Before 2026-10-26 a close was priced on the natural side, and nothing held the
price inside what the structure can be worth. On a wide-quoted expiry-day book the far touch could
sit past the payoff range: a long butterfly closed at a positive exit debit (paying to be rid of a
structure that can never be worth less than zero), an iron fly bought back for more than its width.
The loss recorded is one no fill could produce. From 2026-10-26 every exit is clamped to
`scanner.exit_debit_bounds`, the range the payoff at expiry allows; this applies the same clamp to the
rows recorded before it.

**What it rewrites.** Only `exit_debit` and `pnl` (gross, `(entry_credit - exit_debit) x 100 x
quantity`) on closed rows whose exit debit lies outside the bounds, in either direction. Entry,
costs, exit reason and timing are untouched. A structure with no bound (a calendar across
expirations, or unbounded payoff) is skipped and counted, never guessed.

**Run once.** A completed run writes `<ledger>.exit-debit-clamped.json` beside the ledger, listing
every changed row's old and new values; a second run refuses. The ledger is copied through SQLite's
backup API to `<ledger>.bak-pre-exit-clamp-<stamp>` first, and the update runs in one transaction.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from cherrypick.earnings import scanner

TOLERANCE = 0.005


def _ledger() -> Path:
    home = os.environ.get("CHERRYPICK_HOME")
    return (Path(home) if home else Path.home() / ".cherrypick") / "data" / "earnings" / "paper_trades.db"


def _marker(ledger: Path) -> Path:
    return ledger.with_name(f"{ledger.name}.exit-debit-clamped.json")


def plan(conn: sqlite3.Connection) -> dict:
    """Every closed row's exit debit against its payoff bounds: the changes, and the rows without one."""
    rows = conn.execute(
        "SELECT order_id, strategy, entry_credit, exit_debit, quantity, pnl, legs_json FROM trades "
        "WHERE closed_at IS NOT NULL AND exit_debit IS NOT NULL"
    ).fetchall()
    changes, unbounded = [], 0
    for r in rows:
        bounds = scanner.exit_debit_bounds(json.loads(r["legs_json"] or "[]"))
        if bounds is None:
            unbounded += 1
            continue
        low, high = bounds
        old = float(r["exit_debit"])
        new = round(min(max(old, low), high), 4) + 0.0  # + 0.0: a clamp to the zero bound is 0, not -0
        if abs(new - old) < TOLERANCE:
            continue
        quantity = r["quantity"] or 1
        pnl = round((float(r["entry_credit"]) - new) * 100 * quantity, 2)
        changes.append(
            {
                "order_id": r["order_id"],
                "strategy": r["strategy"],
                "exit_debit": [old, new],
                "pnl": [r["pnl"], pnl],
            }
        )
    return {"rows": len(rows), "unbounded": unbounded, "changes": changes}


def _backup(ledger: Path) -> Path:
    dest = ledger.with_name(f"{ledger.name}.bak-pre-exit-clamp-{datetime.now():%Y%m%d-%H%M%S}")
    src = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    out = sqlite3.connect(dest)
    src.backup(out)
    out.close()
    src.close()
    return dest


def apply(ledger: Path, changes: list[dict]) -> None:
    conn = sqlite3.connect(ledger)
    with conn:
        conn.executemany(
            "UPDATE trades SET exit_debit = ?, pnl = ? WHERE order_id = ? AND exit_debit = ?",
            [(c["exit_debit"][1], c["pnl"][1], c["order_id"], c["exit_debit"][0]) for c in changes],
        )
    conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--apply", action="store_true", help="back up the ledger, then correct it (default: dry run)"
    )
    args = ap.parse_args()
    ledger = _ledger()
    if not ledger.exists():
        print(f"no ledger at {ledger}")
        return 1
    marker = _marker(ledger)
    if marker.exists():
        print(f"already applied ({marker.name}); refusing to clamp twice")
        return 2

    conn = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    p = plan(conn)
    conn.close()
    delta = round(sum(c["pnl"][1] - (c["pnl"][0] or 0.0) for c in p["changes"]), 2)
    print(f"{ledger.name}: {p['rows']} closed rows; {p['unbounded']} have no payoff bound (skipped)")
    print(f"  {len(p['changes'])} exits move inside their bounds, gross P&L {delta:+,.2f}")
    for c in p["changes"]:
        print(
            f"    {c['order_id'][:52]:52s} {c['exit_debit'][0]:>8.3f} -> {c['exit_debit'][1]:>8.3f}"
            f"  pnl {c['pnl'][0]} -> {c['pnl'][1]}"
        )
    if not args.apply:
        print("\ndry run -- nothing written. Re-run with --apply.")
        return 0

    print(f"backup -> {_backup(ledger).name}")
    apply(ledger, p["changes"])
    marker.write_text(
        json.dumps(
            {
                "applied_at": datetime.now().isoformat(timespec="seconds"),
                "rows_changed": len(p["changes"]),
                "gross_pnl_change": delta,
                "unbounded_skipped": p["unbounded"],
                "changes": p["changes"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"  rewrote {len(p['changes'])} row(s); journal -> {marker.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
