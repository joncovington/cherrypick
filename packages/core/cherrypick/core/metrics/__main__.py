"""`python -m cherrypick.core.metrics` — the shared calibration-reading CLI.

A JSON-in/JSON-out bridge over `ledgers.READERS` + `profiles.compare_profiles` +
`calibration_reading`, for a read-only TypeScript surface (the console) that cannot import Python
directly -- same pattern as `cherrypick.core.auth` and `cherrypick.core.calendar`'s own
`__main__.py`. No new metric logic: this is the same normalise-then-summarize path
`orchestrator.report`/`calibrate` already run, pointed at one schema's ledger and grouped by its
own profile tag.

Command:
    read --db PATH --schema {meic_ic|earnings|fly_book|dc_week|pmcc_99|curve_vx|bwb_132}
         [--start YYYY-MM-DD] [--end YYYY-MM-DD]

Output: {"ok": true, "schema": ..., "n_records": N,
         "groups": {tag: {"reading": <calibration_reading>,
                           "session_nets": [[session, net], ...],
                           "trade_nets": [net, ...]}}}
An unknown schema or an unreadable db returns {"ok": false, "error": ...} rather than a traceback
crossing the subprocess boundary -- the console renders `error` on the card head, per its own
"never silent" data rule.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from cherrypick.core import ledgers
from cherrypick.core.profiles import compare_profiles

from . import calibration_reading, session_nets_dated


def cmd_read(args) -> dict:
    reader = ledgers.READERS.get(args.schema)
    if reader is None:
        return {"ok": False, "error": f"unknown schema {args.schema!r}"}
    try:
        conn = ledgers.connect_ro(args.db)
        try:
            records = reader(conn, args.start, args.end)
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        return {"ok": False, "error": f"cannot read {args.db!r}: {exc}"}

    def summarize(group: list) -> dict:
        return {
            "reading": calibration_reading(group),
            "session_nets": [list(pair) for pair in session_nets_dated(group)],
            "trade_nets": [round(r["net_pnl"], 2) for r in group],
        }

    groups = compare_profiles(records, tag_key="profile", summarize=summarize)
    return {"ok": True, "schema": args.schema, "n_records": len(records), "groups": groups}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cherrypick.core.metrics", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rd = sub.add_parser("read")
    rd.add_argument("--db", required=True)
    rd.add_argument("--schema", required=True, choices=sorted(ledgers.READERS))
    rd.add_argument("--start", default=None)
    rd.add_argument("--end", default=None)
    args = ap.parse_args(argv)
    fn = {"read": cmd_read}[args.cmd]
    result = fn(args)
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
