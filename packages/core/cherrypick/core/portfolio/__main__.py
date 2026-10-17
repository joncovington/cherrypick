"""`python -m cherrypick.core.portfolio` -- the portfolio reading of a chosen set of arms, as JSON.

Command:
    read --manifest JSON --start YYYY-MM-DD [--end YYYY-MM-DD]
         [--period day|week] [--weights equal_risk|booked]

`--manifest` is a JSON list of {"key", "db", "schema", "tag", "era"}: one entry per arm, `tag` the
metrics group tag (`<arm>` or `<arm>@<experiment>`), `era` the module's stamped era or null. Each arm
is read exactly as `python -m cherrypick.core.metrics read` reads it (`ledgers.READERS`, the era
filter, `session_nets_dated`), so a session net here is the P&L calendar's cell.

Output: {"ok": true, **portfolio(...), "failures": [{key, error}]}. An arm whose ledger cannot be
read is named under `failures` and left out, never read as zeros.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from cherrypick.core import ledgers
from cherrypick.core import metrics as _metrics

from . import portfolio


def _arm_series(entry: dict, start: str, end: str) -> dict[str, float]:
    schema = ledgers.canonical_schema(entry["schema"])
    reader = ledgers.READERS.get(schema)
    if reader is None:
        raise ValueError(f"unknown schema {entry['schema']!r}")
    conn = ledgers.connect_ro(entry["db"])
    try:
        records = reader(conn, start, end)
    finally:
        conn.close()
    era = entry.get("era")
    if era and era != "ALL" and schema in ledgers.ERA_SCHEMAS:
        records = [r for r in records if r.get("era") == era]
    mine = []
    for r in records:
        exp = r.get("experiment_id")
        if (f"{r['arm']}@{exp}" if exp else r["arm"]) == entry["tag"]:
            mine.append(r)
    return dict(_metrics.session_nets_dated(mine))


def cmd_read(args) -> dict:
    try:
        manifest = json.loads(args.manifest)
    except ValueError as exc:
        return {"ok": False, "error": f"manifest is not JSON: {exc}"}
    series, failures = {}, []
    for entry in manifest:
        try:
            series[entry["key"]] = _arm_series(entry, args.start, args.end)
        except (sqlite3.Error, ValueError, KeyError) as exc:
            failures.append({"key": entry.get("key"), "error": str(exc)})
    return {
        "ok": True,
        **portfolio(series, start=args.start, end=args.end, period=args.period, weights=args.weights),
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cherrypick.core.portfolio", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rd = sub.add_parser("read")
    rd.add_argument("--manifest", required=True)
    rd.add_argument("--start", required=True)
    rd.add_argument("--end", default=None, help="default: the latest close any arm made")
    rd.add_argument("--period", choices=("day", "week"), default="day")
    rd.add_argument("--weights", choices=("equal_risk", "booked"), default="equal_risk")
    args = ap.parse_args(argv)
    result = cmd_read(args)
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
