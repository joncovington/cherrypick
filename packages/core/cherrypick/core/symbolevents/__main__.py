"""python -m cherrypick.core.symbolevents write (--all | --symbols SPY,QQQ) [--no-dolt]

Writes `symbol_events.json` for the symbols named, or with `--all` for every symbol the technicals
store holds bars for; prints a one-line JSON summary. Dolt is reached the way the source registry
reaches it; with `--no-dolt`, or without the connector, earnings stay uncovered.
"""

from __future__ import annotations

import argparse
import json

from cherrypick.core.sources.__main__ import _dolt_or_none

from . import LABELS, stored_symbols, write


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cherrypick.core.symbolevents")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write")
    which = w.add_mutually_exclusive_group(required=True)
    which.add_argument("--symbols", help="comma-separated")
    which.add_argument("--all", action="store_true", help="every symbol in the technicals store")
    w.add_argument("--no-dolt", action="store_true")
    args = ap.parse_args(argv)
    symbols = stored_symbols() if args.all else [s.strip() for s in args.symbols.split(",") if s.strip()]
    doc = write(symbols, connect=_dolt_or_none(args.no_dolt))
    print(json.dumps({"ok": True, **{label: len(doc["labels"][label]) for label in LABELS}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
