"""Move the one paper trade out of the live MEIC ledger into an archive ledger of its own (2026-10-14).

`data/meic/meic_trades.db` is the live book, but its first row is the agent's 2026-06-25 paper
session (`is_paper = 1`, order id `PAPER-20260625-101529-738827`), written before paper had a ledger
of its own. Nothing that reads the live book filters on `is_paper` -- the console's live view counts
it in MEIC's live totals -- so the row leaves the ledger rather than gaining a filter every reader
would have to remember.

It does not go into `paper_trades.db` either: that ledger starts on 2026-07-10 under its own eras,
and an agent-driven June trade appended there would change what paper's `ALL` totals measure. It
goes to `data/meic/paper_agent_2026-06.db`, the `ic_trades` row and its 46 `paper_marks` rows (a
table nothing in the suite reads, holding only this trade), with the live ledger's own table
definitions. The 06-25 `loop_log` and `daily_summary` rows stay: they are the session's narrative
and the account's NLV, not a trade.

Refuses unless the live ledger still holds exactly that one paper row and the archive does not
exist yet. Dry run by default; --apply backs the live ledger up beside itself, writes and checks the
archive, and only then deletes from the live ledger.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime
from pathlib import Path

ORDER_ID = "PAPER-20260625-101529-738827"
ARCHIVE_NAME = "paper_agent_2026-06.db"
TABLES = ("ic_trades", "paper_marks")


def move(meic_dir: Path, apply: bool) -> int:
    live_path = meic_dir / "meic_trades.db"
    archive_path = meic_dir / ARCHIVE_NAME
    live = sqlite3.connect(live_path)

    refused = []
    paper_rows = live.execute("SELECT id, ic_order_id FROM ic_trades WHERE is_paper = 1").fetchall()
    if [r[1] for r in paper_rows] != [ORDER_ID]:
        refused.append(f"live ledger's paper rows are {paper_rows!r}, expected exactly {ORDER_ID}")
    if archive_path.exists():
        refused.append(f"{archive_path.name} already exists")
    counts = {
        t: live.execute(f"SELECT COUNT(*) FROM {t} WHERE ic_order_id = ?", (ORDER_ID,)).fetchone()[0]
        for t in TABLES
    }
    stray = live.execute("SELECT COUNT(*) FROM paper_marks WHERE ic_order_id != ?", (ORDER_ID,)).fetchone()[0]

    print(f"move {ORDER_ID}: " + ", ".join(f"{n} {t} row(s)" for t, n in counts.items()))
    print(f"  from {live_path}\n  to   {archive_path}")
    if stray:
        print(f"  note: paper_marks holds {stray} row(s) for other orders; they stay")
    for r in refused:
        print(f"REFUSED {r}")
    if refused:
        live.close()
        return 2
    if not apply:
        print("DRY RUN: nothing written (--apply to write)")
        live.close()
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = live_path.with_name(f"{live_path.name}.bak-pre-paper-row-move-{stamp}")
    with sqlite3.connect(backup) as dst:
        live.backup(dst)

    live.execute("ATTACH DATABASE ? AS archive", (str(archive_path),))
    with live:
        for t in TABLES:
            ddl = live.execute(
                "SELECT sql FROM main.sqlite_master WHERE type = 'table' AND name = ?", (t,)
            ).fetchone()[0]
            live.execute(ddl.replace(f"CREATE TABLE {t}", f"CREATE TABLE archive.{t}", 1))
            live.execute(f"INSERT INTO archive.{t} SELECT * FROM main.{t} WHERE ic_order_id = ?", (ORDER_ID,))
    copied = {t: live.execute(f"SELECT COUNT(*) FROM archive.{t}").fetchone()[0] for t in TABLES}
    if copied != counts:
        live.close()
        print(f"REFUSED archive holds {copied}, expected {counts}; live ledger untouched")
        return 2
    with live:
        for t in TABLES:
            live.execute(f"DELETE FROM main.{t} WHERE ic_order_id = ?", (ORDER_ID,))
    live.execute("DETACH DATABASE archive")
    live.close()
    print(f"APPLIED: archived {copied} and removed them from the live ledger (backup: {backup.name})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--home", help="cherrypick home (default: ~/.cherrypick or $CHERRYPICK_HOME)")
    args = ap.parse_args()
    home = Path(args.home or os.environ.get("CHERRYPICK_HOME") or Path.home() / ".cherrypick")
    meic_dir = home / "data" / "meic"
    if not (meic_dir / "meic_trades.db").exists():
        print(f"no live ledger in {meic_dir}")
        return 1
    return move(meic_dir, args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
