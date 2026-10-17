"""`python -m cherrypick.core.analysis` — the strategy-analysis CLI.

A JSON-in/JSON-out bridge over `ledgers.READERS` and `arm_reading`, for the console, which cannot
import Python -- the same pattern, arguments and grouping as `python -m cherrypick.core.metrics`,
so the two never disagree about which records make up an arm.

Command:
    read --db PATH --schema SCHEMA [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--era ERA]

    [--module NAME] [--allocations PATH]

`--era` keeps only records stamped with that era, as in the metrics CLI. `--module` names whose
declared allocations (`capital.load_allocations`) apply; without it none do.

Output: {"ok": true, "schema": ..., "version": N, "n_records": N,
         "allocations": {"module", "path", "error"}, "groups": {tag: <arm_reading>}}
where `tag` is the arm, or `<arm>@<experiment_id>` for an advised row stamped with its experiment.
An unknown schema or an unreadable db returns {"ok": false, "error": ...}, never a traceback.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from cherrypick.core import ledgers
from cherrypick.core.profiles import group_by_tag

from . import VERSION, arm_reading, capital


def cmd_read(args) -> dict:
    reader = ledgers.READERS.get(ledgers.canonical_schema(args.schema))
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
    # The module's own stamped era, refused for a schema whose records carry none -- the metrics
    # CLI's rule, so a console slide scoped by era reads the same rows through either bridge.
    era = getattr(args, "era", None)
    if era and era != "ALL":
        if ledgers.canonical_schema(args.schema) not in ledgers.ERA_SCHEMAS:
            return {
                "ok": False,
                "error": f"schema {args.schema!r} records carry no era; --era cannot scope it",
            }
        records = [r for r in records if r.get("era") == era]
    # Grouped as core.metrics' CLI groups them (see its cmd_read): one group per experiment on an
    # advised arm, so the two bridges always describe the same set of records.
    for r in records:
        exp = r.get("experiment_id")
        r["group_tag"] = f"{r['arm']}@{exp}" if exp else r["arm"]
    # The module's declared allocations, by arm. A malformed file applies nothing and says why at
    # the top level: the rest of the analysis does not depend on it, so it is not a refusal.
    module = getattr(args, "module", None)
    alloc_path = getattr(args, "allocations", None)
    allocations = {"module": module, "path": str(alloc_path or capital.allocation_path()), "error": None}
    declared: dict = {}
    if module:
        try:
            declared = capital.load_allocations(Path(alloc_path) if alloc_path else None).get(module, {})
        except capital.AllocationError as exc:
            allocations["error"] = str(exc)

    def summarize(group: list) -> dict:
        # Every record in a group shares its arm; an experiment's book reads its base arm's line.
        return arm_reading(group, declared.get(group[0]["arm"]))

    groups = group_by_tag(records, tag_key="group_tag", summarize=summarize)
    n = len(records)
    return {
        "ok": True,
        "schema": args.schema,
        "version": VERSION,
        "n_records": n,
        "allocations": allocations,
        "groups": groups,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cherrypick.core.analysis", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rd = sub.add_parser("read")
    rd.add_argument("--db", required=True)
    rd.add_argument(
        "--schema", required=True, choices=sorted(set(ledgers.READERS) | set(ledgers.SCHEMA_ALIASES))
    )
    rd.add_argument("--start", default=None)
    rd.add_argument("--end", default=None)
    rd.add_argument("--era", default=None, help="one stamped era (ERA_SCHEMAS only), or ALL")
    rd.add_argument("--module", default=None, help="whose declared allocations apply (arm_capital.json)")
    rd.add_argument("--allocations", default=None, help="the allocation file, if not the default")
    args = ap.parse_args(argv)
    result = cmd_read(args)
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
