"""`python -m cherrypick.core.metrics` — the shared calibration-reading CLI.

A JSON-in/JSON-out bridge over `ledgers.READERS` + `profiles.group_by_tag` +
`calibration_reading`, for a read-only TypeScript surface (the console) that cannot import Python
directly -- same pattern as `cherrypick.core.auth` and `cherrypick.core.calendar`'s own
`__main__.py`. No new metric logic: this is the same normalise-then-summarize path
`orchestrator.report`/`calibrate` already run, pointed at one schema's ledger and grouped by its
own profile tag.

Command:
    read --db PATH --schema {meic_ic|earnings|fly_book|dc_week|pmcc|curve_vx|bwb_132}
         [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--era ERA]

`--era` keeps only records stamped with that era (a schema in `ledgers.ERA_SCHEMAS`; any other
schema is refused rather than silently unfiltered). An unstamped row never matches. `ALL`, or no
flag, applies no era filter.

Output: {"ok": true, "schema": ..., "n_records": N,
         "groups": {tag: {"reading": <calibration_reading>,
                           "session_nets": [[session, net], ...],
                           "trade_nets": [net, ...]}}}
where `tag` is the record's profile, or `<profile>@<experiment_id>` for an advised row stamped
with the experiment it ran under (see cmd_read).

    trades --db PATH --schema SCHEMA [--start --end --era] [--tag TAG]

The same records, one row each (`TRADE_KEYS` plus `tag`), sorted by session and entry time:
{"ok": true, "schema": ..., "era": ..., "trades": [...]}. A session's rows under one tag sum to
that tag's `session_nets` entry for the session, because both come from one read (`_records`).
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
from cherrypick.core.profiles import group_by_tag

from . import calibration_reading, session_nets_dated


def _records(args) -> tuple[list[dict] | None, dict | None]:
    """`(records, None)`, each carrying its `group_tag`, or `(None, refusal)`. The one read both
    commands share, so a `trades` listing is exactly the records behind a `read` group's nets."""
    reader = ledgers.READERS.get(ledgers.canonical_schema(args.schema))
    if reader is None:
        return None, {"ok": False, "error": f"unknown schema {args.schema!r}"}
    try:
        conn = ledgers.connect_ro(args.db)
        try:
            records = reader(conn, args.start, args.end)
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        return None, {"ok": False, "error": f"cannot read {args.db!r}: {exc}"}
    era = getattr(args, "era", None)
    if era and era != "ALL":
        if ledgers.canonical_schema(args.schema) not in ledgers.ERA_SCHEMAS:
            why = f"schema {args.schema!r} records carry no era; --era cannot scope it"
            return None, {"ok": False, "error": why}
        records = [r for r in records if r.get("era") == era]
    # One group per EXPERIMENT for stamped advised rows (2026-09-16): the `advised:<base>` tag
    # names an arm, and every experiment on that base reuses it in turn, so grouping by tag
    # alone pooled three experiments' rows into one line. Rows carrying an experiment_id group
    # under `<tag>@<experiment_id>`; rows written before the stamp existed (experiment_id None)
    # stay under the bare tag, which the console labels as unstamped history rather than
    # inferring their experiment from dates. Display grouping only: `arm` on the record is
    # untouched, so calibrate/report/verdicts see exactly what they always did.
    for r in records:
        exp = r.get("experiment_id")
        r["group_tag"] = f"{r['arm']}@{exp}" if exp else r["arm"]
    return records, None


def cmd_read(args) -> dict:
    records, refusal = _records(args)
    if refusal is not None:
        return refusal
    era = getattr(args, "era", None)

    def summarize(group: list) -> dict:
        return {
            "reading": calibration_reading(group),
            "session_nets": [list(pair) for pair in session_nets_dated(group)],
            # An unrecorded net (n/r) is no trade's result; the reading's `unscored` counts it.
            "trade_nets": [round(r["net_pnl"], 2) for r in group if r.get("net_pnl") is not None],
        }

    groups = group_by_tag(records, tag_key="group_tag", summarize=summarize)
    return {"ok": True, "schema": args.schema, "era": era, "n_records": len(records), "groups": groups}


#: What a `trades` row carries: the record's own fields, renamed nowhere.
TRADE_KEYS = (
    "symbol",
    "strategy",
    "session",
    "entry_time",
    "exit_time",
    "entry_spot",
    "exit_spot",
    "gross_pnl",
    "fees",
    "settlement",
    "slippage",
    "slippage_basis",
    "net_pnl",
)


def cmd_trades(args) -> dict:
    """The records behind `read`'s groups, one row each, optionally one tag's -- for a drill from a
    session's net to the trades that made it. Values are passed through unrounded and unrenamed;
    a field the schema does not record is None (n/r), never zero."""
    records, refusal = _records(args)
    if refusal is not None:
        return refusal
    tag = getattr(args, "tag", None)
    rows = [
        {"tag": r["group_tag"], **{k: r.get(k) for k in TRADE_KEYS}}
        for r in records
        if tag is None or r["group_tag"] == tag
    ]
    rows.sort(key=lambda t: (str(t["session"] or ""), str(t["entry_time"] or ""), t["tag"]))
    return {"ok": True, "schema": args.schema, "era": getattr(args, "era", None), "trades": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cherrypick.core.metrics", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("read", "trades"):
        p = sub.add_parser(name)
        p.add_argument("--db", required=True)
        p.add_argument(
            "--schema", required=True, choices=sorted(set(ledgers.READERS) | set(ledgers.SCHEMA_ALIASES))
        )
        p.add_argument("--start", default=None)
        p.add_argument("--end", default=None)
        p.add_argument("--era", default=None, help="one stamped era (ERA_SCHEMAS only), or ALL")
        if name == "trades":
            p.add_argument("--tag", default=None, help="one group's trades (an arm, or <arm>@<experiment>)")
    args = ap.parse_args(argv)
    fn = {"read": cmd_read, "trades": cmd_trades}[args.cmd]
    result = fn(args)
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
