"""Re-cost earnings' past settlements under the per-strike expiry fee (2026-09-24).

    python scripts/earnings_resettle_expiry_fees.py            # dry run -- prints, changes nothing
    python scripts/earnings_resettle_expiry_fees.py --apply    # backs up the ledger, then corrects it

Runnable from anywhere; the ledger resolves off `$CHERRYPICK_HOME` (default `~/.cherrypick`). Needs
the same settlement closes the loop used (`settlement.settlement_close`, the Dolt price history).

**What was wrong.** A settled position's expired legs went through the ordinary closing-cost stack
as if bought back at intrinsic: clearing and regulatory fees on every expired contract -- on the
worthless ones too, which never transact -- and nothing for the exercise/assignment event itself.
Fixed in the loop (`settlement.settlement_fee`): $5 per ITM expired option symbol, per strike and
never per contract, nothing for a worthless leg; the still-listed legs of a calendar keep the stack.

**What it rewrites.** Only `trades.exit_cost` on rows closed by settlement (`exit_reason` `expired`
or `front_expiry`). The correction is exact without the day's quotes: the closing commission is $0
and an expired leg has no width, so what the old path charged on the expired legs was precisely
their pass-through fees -- `apply_exit_costs` over those legs alone at a zero-width quote. That
comes off; `settlement_fee` at the expiration's own settlement close goes on. The live legs' cost,
slippage included, is untouched. `pnl` is gross and is not touched. A row whose settlement close
cannot be read is skipped and counted.

**Run once.** A completed run writes `<ledger>.expiry-fee-resettled.json` beside the ledger and a
second run refuses. The ledger is copied through SQLite's backup API to
`<ledger>.bak-pre-expiry-fee-<stamp>` first.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from cherrypick.earnings import costs, paper_loop, provider, scanner, settlement

SETTLED = ("expired", "front_expiry")


def _ledger() -> Path:
    home = os.environ.get("CHERRYPICK_HOME")
    return (Path(home) if home else Path.home() / ".cherrypick") / "data" / "earnings" / "paper_trades.db"


def _marker(ledger: Path) -> Path:
    return ledger.with_name(f"{ledger.name}.expiry-fee-resettled.json")


def plan(conn: sqlite3.Connection, config: dict) -> dict:
    marks = ",".join("?" * len(SETTLED))
    rows = conn.execute(
        f"SELECT order_id, symbol, quantity, legs_json, exit_cost, closed_at FROM trades "
        f"WHERE exit_reason IN ({marks})",
        SETTLED,
    ).fetchall()
    changes, skipped, itm_strikes = [], [], 0
    closes: dict[tuple, float | None] = {}
    for r in rows:
        legs = json.loads(r["legs_json"] or "[]")
        closed = datetime.fromtimestamp(float(r["closed_at"]), paper_loop.ET).date()
        expired, _live = settlement.split_legs(legs, closed)
        quotes = {}
        for leg in expired:
            expiry = provider.expiry_from_occ(leg["symbol"])
            key = (r["symbol"], expiry)
            if key not in closes:
                closes[key] = settlement.settlement_close(r["symbol"], expiry, config) if expiry else None
            close = closes[key]
            value = None if close is None else settlement.option_intrinsic(leg["symbol"], close)
            if value is None:
                break
            quotes[leg["symbol"]] = {"bid": value, "ask": value, "mid": value}
        if not expired or len(quotes) != len(expired):
            skipped.append(r["order_id"])
            continue
        quantity = r["quantity"] or 1
        charged = costs.apply_exit_costs(
            {"order": {"legs": expired}}, [quotes[leg["symbol"]] for leg in expired], quantity, config
        )["total_cost"]
        fee = settlement.settlement_fee([leg["symbol"] for leg in expired], quotes)
        itm_strikes += int(round(fee / 5.0))
        new = round((r["exit_cost"] or 0.0) - charged + fee, 2)
        changes.append((r["order_id"], r["exit_cost"], new))
    return {"rows": len(rows), "changes": changes, "skipped": skipped, "itm_strikes": itm_strikes}


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
        print(f"already applied ({marker.name}); refusing to re-cost twice")
        return 2

    config = scanner._load_config()
    conn = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    p = plan(conn, config)
    conn.close()
    moved = [(oid, old, new) for oid, old, new in p["changes"] if abs((old or 0.0) - new) >= 0.005]
    delta = round(sum(new - (old or 0.0) for _, old, new in p["changes"]), 2)
    print(f"{ledger.name}: {p['rows']} rows closed by settlement")
    print(
        f"  {len(moved)} exit_cost values move, net {delta:+,.2f};"
        f" {p['itm_strikes']} ITM strikes charged $5 each"
    )
    print(f"  {len(p['skipped'])} skipped: settlement close unavailable")
    for oid, old, new in moved[:8]:
        print(f"    {oid[:40]:40s} {old} -> {new}")
    if not args.apply:
        print("\ndry run -- nothing written. Re-run with --apply.")
        return 0

    print(f"backup -> {_backup(ledger).name}")
    conn = sqlite3.connect(ledger)
    with conn:
        conn.executemany(
            "UPDATE trades SET exit_cost = ? WHERE order_id = ?", [(new, oid) for oid, _, new in moved]
        )
    conn.close()
    marker.write_text(
        json.dumps(
            {
                "applied_at": datetime.now().isoformat(timespec="seconds"),
                "rows_changed": len(moved),
                "net_exit_cost_change": delta,
                "itm_strikes": p["itm_strikes"],
                "skipped": len(p["skipped"]),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"  rewrote {len(moved)} row(s); marker -> {marker.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
