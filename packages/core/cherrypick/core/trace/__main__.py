"""`python -m cherrypick.core.trace` -- one position's entry and management trace as JSON.

Command:
    read --db PATH --schema SCHEMA --position-id ID

Output: {"ok": true, "schema": ..., "position_id": ..., **trace(...)}; an unreadable db is
{"ok": false, "error": ...}, never a traceback. A part a module does not record reads
`recorded: false` with the reason: an answer, not a failure.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from cherrypick.core import ledgers

from . import trace


def cmd_read(args) -> dict:
    try:
        conn = ledgers.connect_ro(args.db)
        try:
            out = trace(conn, args.schema, args.position_id)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {"ok": False, "error": f"cannot read {args.db!r}: {exc}"}
    return {"ok": True, "schema": args.schema, "position_id": args.position_id, **out}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cherrypick.core.trace", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rd = sub.add_parser("read")
    rd.add_argument("--db", required=True)
    rd.add_argument(
        "--schema", required=True, choices=sorted(set(ledgers.READERS) | set(ledgers.SCHEMA_ALIASES))
    )
    rd.add_argument("--position-id", required=True)
    args = ap.parse_args(argv)
    result = cmd_read(args)
    print(json.dumps(result, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
