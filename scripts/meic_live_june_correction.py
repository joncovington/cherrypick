"""Correct the two June live MEIC rows whose credit is the order's limit, not its fill (2026-10-14).

The live ledger (`data/meic/meic_trades.db`) holds the agent-driven live trades of 2026-06-29 and
06-30, written before `_confirm_fills` existed. Each row kept the LIMIT it was submitted at as
`net_credit`, although the loop recorded the fill the same day. The broker cannot confirm either
fill now: those orders are not on the account this login holds (`record_not_found`; no XSP
transactions before 2026-07-30), so every figure here comes from the ledger's own records of that
day, and each row is marked as not broker-verified.

  - 06-30 (id 7, order 479793516). Submitted at 0.32; the stop was re-based at fill confirmation
    to 0.41 trigger / 0.43 limit, which is 0.95 x 0.43, and the EOD report states the fill at 0.43.
    Credit 0.43 less the 0.39 close is the recorded +$4.00 gross, and +$4.00 - $4.96 fees matches
    the day's NLV move (1,732.39 -> 1,731.43). Only `net_credit` changes; pnl and fees stand.
  - 06-29 (id 6, order 479518187). Submitted at 0.36; the loop's entry row says "filled at $0.38
    net credit", and the stops were set at 0.36 trigger (0.95 x 0.38). The exits are recorded leg by
    leg: call spread 0.41 - 0.03 = 0.38, put spread 0.04 - 0.02 = 0.02, 0.40 in all. Gross is
    (0.38 - 0.40) x 100 = -$2.00; the -$4.00 in its exit note used the limit. `pnl` was never
    written, and is set; fees were never recorded and stay NULL (n/r), not a modelled figure. The
    day's NLV move (-$6.97) is consistent with -$2.00 and a $4.96-style fee, but that is
    corroboration, not a record.

Each row's `exit_analysis` keeps what it said and gains a `ledger_correction` entry naming the
change, the evidence and `broker_verified: false`. Refuses unless every row still holds exactly the
values being corrected, so it cannot run twice or over a row that has since changed. Dry run by
default; --apply backs the ledger up beside itself first.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

CORRECTED_ON = "2026-10-14"

# id -> (the values the row must hold now, the values it gets, the evidence)
CORRECTIONS = {
    7: (
        {"ic_order_id": "479793516", "net_credit": 0.32, "exit_price": 0.39, "pnl": 4.0, "fees": 4.96},
        {"net_credit": 0.43},
        "net_credit held the 0.32 limit; the stop was re-based at fill confirmation to 0.41/0.43 "
        "(0.95 x 0.43) and the 2026-06-30 EOD report states the fill at 0.43. 0.43 - 0.39 = the "
        "recorded +$4.00 gross; +$4.00 - $4.96 fees = the day's NLV move, 1,732.39 -> 1,731.43.",
    ),
    6: (
        {"ic_order_id": "479518187", "net_credit": 0.36, "exit_price": 0.4, "pnl": None, "fees": None},
        {"net_credit": 0.38, "pnl": -2.0},
        "net_credit held the 0.36 limit; loop_log 2026-06-29 10:56 records 'filled at $0.38 net "
        "credit' and the stops were set at 0.36 (0.95 x 0.38). Exit legs as recorded: call "
        "0.41 - 0.03, put 0.04 - 0.02, 0.40 in all, so gross (0.38 - 0.40) x 100 = -$2.00. The "
        "-$4.00 above used the limit. Fees were never recorded and stay n/r.",
    ),
}


def _matches(row: sqlite3.Row, expect: dict) -> list[str]:
    bad = []
    for col, want in expect.items():
        have = row[col]
        if want is None or have is None:
            same = have is want
        elif isinstance(want, float):
            same = abs(float(have) - want) < 1e-9
        else:
            same = have == want
        if not same:
            bad.append(f"{col}={have!r} (expected {want!r})")
    return bad


def _note(existing: str | None, change: dict, before: dict, evidence: str) -> str:
    entry = {
        "corrected_on": CORRECTED_ON,
        "changed": {k: {"from": before[k], "to": v} for k, v in change.items()},
        "evidence": evidence,
        "broker_verified": False,
        "why_unverified": "the order is not on the account this login holds (record_not_found)",
    }
    try:
        doc = json.loads(existing) if existing else {}
        if not isinstance(doc, dict):
            doc = {"original": doc}
    except json.JSONDecodeError:
        doc = {"original": existing}
    doc["ledger_correction"] = entry
    return json.dumps(doc)


def correct(db_path: Path, apply: bool) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    plan, refused = [], []
    for row_id, (expect, change, evidence) in CORRECTIONS.items():
        row = conn.execute("SELECT * FROM ic_trades WHERE id = ?", (row_id,)).fetchone()
        if row is None:
            refused.append(f"id {row_id}: no such row")
            continue
        bad = _matches(row, expect)
        if bad:
            refused.append(f"id {row_id}: " + ", ".join(bad))
            continue
        before = {k: row[k] for k in change}
        note = _note(row["exit_analysis"], change, before, evidence)
        plan.append((row_id, row["trade_date"], change, before, note))

    for row_id, day, change, before, _ in plan:
        diffs = ", ".join(f"{k} {before[k]!r} -> {v!r}" for k, v in change.items())
        print(f"id {row_id} ({day}): {diffs}; exit_analysis gains ledger_correction")
    for r in refused:
        print(f"REFUSED {r}")
    if refused:
        conn.close()
        return 2
    if not apply:
        print("DRY RUN: nothing written (--apply to write)")
        conn.close()
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = db_path.with_name(f"{db_path.name}.bak-pre-june-correction-{stamp}")
    with sqlite3.connect(backup) as dst:
        conn.backup(dst)
    with conn:
        for row_id, _, change, _, note in plan:
            sets = ", ".join(f"{k} = ?" for k in change) + ", exit_analysis = ?, updated_at = ?"
            conn.execute(
                f"UPDATE ic_trades SET {sets} WHERE id = ?",
                [*change.values(), note, datetime.now().astimezone().isoformat(sep=" "), row_id],
            )
    conn.close()
    print(f"APPLIED to {db_path} (backup: {backup.name})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--home", help="cherrypick home (default: ~/.cherrypick or $CHERRYPICK_HOME)")
    args = ap.parse_args()
    home = Path(args.home or os.environ.get("CHERRYPICK_HOME") or Path.home() / ".cherrypick")
    db = home / "data" / "meic" / "meic_trades.db"
    if not db.exists():
        print(f"no live ledger at {db}")
        return 1
    return correct(db, args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
