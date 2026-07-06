"""Ingests EarningsEdgeDetection scanner output and exposes candidates as JSON.

Not yet implemented. Intended commands (see CLAUDE.md's Tool Reference):
  get_candidates --date MM/DD/YYYY
"""

import argparse
import json
import sys


def cmd_get_candidates(args) -> dict:
    raise NotImplementedError("parse EarningsEdgeDetection --iron-fly output for the given date")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_candidates = sub.add_parser("get_candidates")
    p_candidates.add_argument("--date", required=True)

    args = parser.parse_args()
    dispatch = {
        "get_candidates": cmd_get_candidates,
    }
    result = dispatch[args.command](args)
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
