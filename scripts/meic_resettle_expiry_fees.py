"""Charge MEIC's past ITM cash settlements the $5-per-strike fee they never paid (2026-09-24).

    python scripts/meic_resettle_expiry_fees.py            # dry run -- prints, changes nothing
    python scripts/meic_resettle_expiry_fees.py --apply    # backs up the ledger, then corrects it

Runnable from anywhere; the ledger resolves off `$CHERRYPICK_HOME` (default `~/.cherrypick`).

**What was wrong.** `paper._apply_exit_decision`'s expiry branch charged nothing ("expiration is not
a transaction"), which is right for an OTM leg and wrong for an ITM one: SPX/XSP cash settlement is
an exercise/assignment event the broker charges $5 for, per option symbol, never per contract. Fixed
in 587c9a17 for every settlement from 2026-09-25 on; this brings the history in line, so the ledger
means one thing on both sides of the fix and no measurement break is needed.

**What it rewrites.** For every row settled by expiry (`exit_reason` `expired_settlement` or
`stopped+expired_settlement`), `fees` gains `paper.expire_fees(n)`, where `n` is
`paper.settlement_itm_strikes` over the sides still held at settlement (a side with a recorded
`*_stop_cost` was bought back and cannot settle) at the recorded `settle_underlying` -- the exact
count and fee the write path now charges. `pnl` is gross and is not touched. A row with no recorded
settlement price is skipped and counted, never charged a guessed zero or a guessed fee.
`daily_summary` is then rebuilt for every day it holds, through MEIC's own idempotent
`rollup_daily_summary`, rather than patched by hand.

**Run once.** There is no per-row marker for a fee already charged, so a completed run writes
`<ledger>.expiry-fee-resettled.json` beside the ledger and a second run refuses. A marker FILE
rather than a `measurement_breaks` row on purpose: a book-wide break row bounds the regime-cuts era
(`cherrypick.core.regimecuts`), and this is a correction, not a change of what the rows measure.
The ledger is copied through SQLite's backup API to `<ledger>.bak-pre-expiry-fee-<stamp>` first.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from cherrypick.meic import paper

SETTLED = ("expired_settlement", "stopped+expired_settlement")


def _ledger() -> Path:
    home = os.environ.get("CHERRYPICK_HOME")
    return (Path(home) if home else Path.home() / ".cherrypick") / "data" / "meic" / "paper_trades.db"


def _marker(ledger: Path) -> Path:
    return ledger.with_name(f"{ledger.name}.expiry-fee-resettled.json")


def plan(conn: sqlite3.Connection) -> dict:
    marks = ",".join("?" * len(SETTLED))
    rows = conn.execute(
        f"SELECT ic_order_id, trade_date, put_strike, call_strike, wing_width, settle_underlying, "
        f"put_stop_cost, call_stop_cost, fees FROM ic_trades WHERE exit_reason IN ({marks})",
        SETTLED,
    ).fetchall()
    charges, unknown, strikes = [], 0, 0
    for r in rows:
        n = paper.settlement_itm_strikes(
            r["put_strike"],
            r["call_strike"],
            r["wing_width"],
            r["settle_underlying"],
            put_open=r["put_stop_cost"] is None,
            call_open=r["call_stop_cost"] is None,
        )
        if n is None:
            unknown += 1
        elif n:
            strikes += n
            charges.append((r["ic_order_id"], paper.expire_fees(n)))
    return {"settled_rows": len(rows), "charges": charges, "strikes": strikes, "unknown_price": unknown}


def _backup(ledger: Path) -> Path:
    dest = ledger.with_name(f"{ledger.name}.bak-pre-expiry-fee-{datetime.now():%Y%m%d-%H%M%S}")
    src = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    out = sqlite3.connect(dest)
    src.backup(out)
    out.close()
    src.close()
    return dest


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
        print(f"already applied ({marker.name}); refusing to charge the fee twice")
        return 2

    conn = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    p = plan(conn)
    days = [d for (d,) in conn.execute("SELECT summary_date FROM daily_summary ORDER BY summary_date")]
    conn.close()
    total = round(sum(fee for _, fee in p["charges"]), 2)
    print(f"{ledger.name}: {p['settled_rows']} rows settled by expiry")
    print(f"  {len(p['charges'])} with ITM strikes: {p['strikes']} strikes, ${total:,.2f} to charge")
    print(f"  {p['unknown_price']} skipped: no recorded settlement price")
    print(f"  daily_summary: {len(days)} day(s) to rebuild")
    if not args.apply:
        print("\ndry run -- nothing written. Re-run with --apply.")
        return 0

    print(f"backup -> {_backup(ledger).name}")
    conn = sqlite3.connect(ledger)
    with conn:
        conn.executemany(
            "UPDATE ic_trades SET fees = ROUND(COALESCE(fees, 0) + ?, 4) WHERE ic_order_id = ?",
            [(fee, oid) for oid, fee in p["charges"]],
        )
    conn.close()
    print(f"  charged {len(p['charges'])} row(s), ${total:,.2f}")

    failed = []
    for day in days:
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "cherrypick.meic.db",
                "--db",
                str(ledger),
                "rollup_daily_summary",
                "--date",
                day,
            ],
            capture_output=True,
            text=True,
        )
        if done.returncode != 0:
            failed.append(day)
    print(
        f"  daily_summary rebuilt for {len(days) - len(failed)} day(s)"
        + (f"; FAILED {failed}" if failed else "")
    )

    marker.write_text(
        json.dumps(
            {
                "applied_at": datetime.now().isoformat(timespec="seconds"),
                "rows_charged": len(p["charges"]),
                "strikes": p["strikes"],
                "fee_total": total,
                "skipped_unknown_price": p["unknown_price"],
                "daily_summary_rebuilt": len(days) - len(failed),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"  marker -> {marker.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
