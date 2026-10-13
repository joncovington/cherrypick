"""Correct the live ledger's measurement break id 5: cap-only sizing began 2026-09-28, not 09-25.

Break 5 (journaled retroactively on 2026-09-28) says the one-incomplete-at-a-time rule was removed
from 2026-09-25. The config changed that day, but the running loop did not: `incomplete_spread_blocks`
refused live entries all session on 09-25 (320 refusals, 10:31-15:59) and has never fired since, and
09-25's peak of concurrently open verticals was 1, against 3 on every session from 09-28. So
09-18..09-25 is the one-incomplete sample and 09-28 onward is cap-only; as recorded, 09-25 is pooled
on the wrong side.

The corrected date is DERIVED from the ledger, not typed in: the first session after the last
`incomplete_spread_blocks` refusal that has a live decision on record. The script refuses to write
unless the row still says what it is expected to say and the derived date is 2026-09-28.

Dry run by default (prints the row before and after); --apply backs the ledger up with SQLite's
backup API to `<ledger>.bak-pre-break5-fix-<stamp>` and then updates the one row. Already corrected
is a no-op. Paths resolve from --db, else $CHERRYPICK_HOME, else ~/.cherrypick.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BREAK_ID = 5
RECORDED_DATE = "2026-09-25"
CORRECT_DATE = "2026-09-28"
REFUSAL = "incomplete_spread_blocks"
REASON_PREFIX = "live concurrency: the one-incomplete-at-a-time rule removed"

CORRECTED_REASON = (
    "live concurrency: the one-incomplete-at-a-time rule removed (live.max_incomplete_spreads null); "
    "max_open_margin_dollars 1000 becomes the sole sizing gate, admitting about three open spreads. "
    "Journaled retroactively on 2026-09-28 and dated 09-25, the day the config changed; corrected to "
    "09-28 on {today}: the running loop still refused on incomplete_spread_blocks all session on 09-25 "
    "({refusals} refusals, {first}-{last}) and never since, and 09-25 peaked at 1 open vertical against "
    "3 from 09-28. Live rows 09-18..09-25 are one incomplete at a time; 09-28.. are cap only."
)


def default_db() -> Path:
    home = Path(os.environ.get("CHERRYPICK_HOME") or Path.home() / ".cherrypick")
    return home / "data" / "flies" / "live_trades.db"


def evidence(conn: sqlite3.Connection) -> dict:
    """What the ledger says about when the one-incomplete rule last bound."""
    last = conn.execute("SELECT MAX(trade_date) FROM fly_decisions WHERE reason = ?", (REFUSAL,)).fetchone()[
        0
    ]
    out = {"last_refusal_session": last, "derived_date": None}
    if last is None:
        return out
    day = conn.execute(
        "SELECT SUM(occurrences), MIN(first_seen), MAX(last_seen) FROM fly_decisions "
        "WHERE reason = ? AND trade_date = ?",
        (REFUSAL, last),
    ).fetchone()
    out.update(refusals=day[0], first=day[1], last=day[2])
    out["derived_date"] = conn.execute(
        "SELECT MIN(trade_date) FROM fly_decisions WHERE trade_date > ?", (last,)
    ).fetchone()[0]
    return out


def plan(conn: sqlite3.Connection, today: str) -> dict:
    """The row as it stands, the row as it would be, and whether it is safe to write."""
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM measurement_breaks WHERE id = ?", (BREAK_ID,)).fetchone()
    if row is None:
        return {"status": "refused", "why": f"no measurement_breaks row id {BREAK_ID}"}
    before = dict(row)
    if before["break_date"] == CORRECT_DATE:
        return {"status": "already_correct", "before": before}
    problems = []
    if before["break_date"] != RECORDED_DATE:
        problems.append(f"break_date is {before['break_date']!r}, expected {RECORDED_DATE!r}")
    if before["scope"] != "live" or not str(before["reason"]).startswith(REASON_PREFIX):
        problems.append("row is not the live-concurrency break this script corrects")
    ev = evidence(conn)
    if ev["last_refusal_session"] != RECORDED_DATE:
        last = ev["last_refusal_session"]
        problems.append(f"last {REFUSAL} session is {last!r}, expected {RECORDED_DATE!r}")
    if ev["derived_date"] != CORRECT_DATE:
        problems.append(f"first session after it is {ev['derived_date']!r}, expected {CORRECT_DATE!r}")
    if problems:
        return {"status": "refused", "why": "; ".join(problems), "before": before, "evidence": ev}

    def hhmm(ts: str) -> str:
        return datetime.fromisoformat(ts).strftime("%H:%M")

    after = {
        **before,
        "break_date": CORRECT_DATE,
        "reason": CORRECTED_REASON.format(
            today=today, refusals=ev["refusals"], first=hhmm(ev["first"]), last=hhmm(ev["last"])
        ),
    }
    return {"status": "ready", "before": before, "after": after, "evidence": ev}


def backup(path: Path) -> Path:
    dest = path.with_name(f"{path.name}.bak-pre-break5-fix-{datetime.now():%Y%m%d-%H%M%S}")
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    return dest


def run(db: Path, *, apply: bool, today: str | None = None) -> dict:
    today = today or datetime.now().date().isoformat()
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        result = plan(ro, today)
    finally:
        ro.close()
    result["db"] = str(db)
    result["applied"] = False
    if not apply or result["status"] != "ready":
        return result
    result["backup"] = str(backup(db))
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(
            "UPDATE measurement_breaks SET break_date = ?, reason = ? WHERE id = ? AND break_date = ?",
            (result["after"]["break_date"], result["after"]["reason"], BREAK_ID, RECORDED_DATE),
        )
        if cur.rowcount != 1:
            conn.rollback()
            result["status"] = "refused"
            result["why"] = "row changed between the read and the write; nothing written"
            return result
        conn.commit()
    finally:
        conn.close()
    result["applied"] = True
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="back up, then write (default: dry run)")
    ap.add_argument("--db", type=Path, help="live ledger (default: <home>/data/flies/live_trades.db)")
    args = ap.parse_args(argv)
    db = args.db or default_db()
    if not db.exists():
        print(f"no ledger at {db}", file=sys.stderr)
        return 2
    result = run(db, apply=args.apply)
    print(json.dumps(result, indent=2, default=str))
    if result["status"] == "refused":
        return 1
    if result["status"] == "ready" and not result["applied"]:
        print("DRY RUN -- nothing written. Re-run with --apply to back up and correct the row.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
