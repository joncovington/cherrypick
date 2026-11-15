"""The source registry's command line.

    python -m cherrypick.core.sources list                       # every declared source
    python -m cherrypick.core.sources inventory [--only NAME,...] [--no-dolt]
    python -m cherrypick.core.sources coverage --field closes --symbol SPY --from 2019-01-02 --to 2026-11-13
        [--cadence 1d] [--expiry monthly] [--licensed]

`inventory` reads every store read-only and writes the cache (`~/.cherrypick/state/sources_inventory.json`);
`coverage` and `list` read only the cache. Output is JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

from . import DENSE, SOURCES, coverage, inventory


def _dolt_connect(database: str):
    """The local Dolt server, as the suite's readers reach it. The connector is the earnings package's
    dependency, imported here only when an inventory asks for Dolt."""
    import mysql.connector

    return mysql.connector.connect(
        host=os.environ.get("CHERRYPICK_DOLT_HOST", "127.0.0.1"),
        port=int(os.environ.get("CHERRYPICK_DOLT_PORT", "3306")),
        user=os.environ.get("CHERRYPICK_DOLT_USER", "root"),
        database=database,
        connection_timeout=10,
    )


def _dolt_or_none(skip: bool):
    if skip:
        return None
    try:
        import mysql.connector  # noqa: F401
    except ImportError:
        print(
            "inventory: the MySQL connector is not installed; Dolt sources keep their last entry",
            file=sys.stderr,
        )
        return None
    return _dolt_connect


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cherrypick.core.sources")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="every declared source")
    inv = sub.add_parser("inventory", help="read every store's spans into the cache")
    inv.add_argument("--only", default=None, help="comma-separated source names; the rest keep their entry")
    inv.add_argument("--no-dolt", action="store_true", help="leave the Dolt sources as last read")
    cov = sub.add_parser("coverage", help="which sources cover a field for a symbol over dates")
    cov.add_argument("--field", required=True)
    cov.add_argument("--symbol", required=True)
    cov.add_argument("--from", dest="start", required=True)
    cov.add_argument("--to", dest="end", required=True)
    cov.add_argument("--cadence", default=None)
    cov.add_argument("--expiry", default=None)
    cov.add_argument("--licensed", action="store_true", help="include the vendor's licensed stores")
    cov.add_argument("--min-density", type=float, default=None, help="accept a thinner source (default 0.95)")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        doc = inventory.load()
        rows = []
        for s in SOURCES:
            e = (doc.get("sources") or {}).get(s.name) or {}
            rows.append({**asdict(s), "inventoried": bool(e) and not e.get("error"), "error": e.get("error")})
        print(json.dumps({"sources": rows, "undeclared": doc.get("undeclared", [])}, indent=1))
        return 0
    if args.cmd == "inventory":
        only = {x.strip() for x in args.only.split(",") if x.strip()} if args.only else None
        doc = inventory.run(
            previous=inventory.load(),
            connect_dolt=_dolt_or_none(args.no_dolt),
            only=only,
            log=lambda m: print(m, file=sys.stderr),
        )
        where = inventory.write(doc)
        failed = sorted(k for k, v in doc["sources"].items() if v.get("error"))
        print(
            json.dumps({"written": str(where), "failed": failed, "undeclared": doc["undeclared"]}, indent=1)
        )
        return 1 if failed else 0
    try:
        out = coverage(
            inventory.load(),
            args.field,
            args.symbol,
            args.start,
            args.end,
            cadence=args.cadence,
            expiry=args.expiry,
            licensed=args.licensed,
            min_density=args.min_density if args.min_density is not None else DENSE,
        )
    except ValueError as exc:
        print(f"coverage: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
