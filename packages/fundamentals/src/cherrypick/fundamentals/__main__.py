"""cherrypick-fundamentals: the fair-value file and the landings that feed it.

    python -m cherrypick.fundamentals valuation [--day YYYY-MM-DD]   the fair-value file (valuation.py)
    python -m cherrypick.fundamentals land sec          the fetched SEC filings (secfacts.py)
    python -m cherrypick.fundamentals land statements   Dolt's annual statements (statements.py)
    python -m cherrypick.fundamentals land consensus    Dolt's revenue consensus (consensus.py)
    python -m cherrypick.fundamentals land all          all three, in that order

Local files and the local Dolt server only; the network is reached by scripts/fetch_sec_facts.py and
scripts/fetch_damodaran.py, never from here.
"""

from __future__ import annotations

import argparse
import json
import sys


def cmd_valuation(args) -> int:
    from . import valuation

    report = valuation.build(day=args.day)
    print(json.dumps(report, indent=1))
    return 0 if report.get("ok") else 1


def cmd_land(args) -> int:
    from . import consensus, secfacts, statements

    steps = {"sec": secfacts.land, "statements": statements.land, "consensus": consensus.land}
    which = list(steps) if args.what == "all" else [args.what]
    out = {name: steps[name]() for name in which}
    out["ok"] = all(r.get("ok") for r in out.values())
    print(json.dumps(out, indent=1))
    return 0 if out["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cherrypick.fundamentals", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    va = sub.add_parser("valuation", help="the fair-value file: one DCF estimate an options-tradable name")
    va.add_argument("--day", default=None, help="ISO date (default: today)")
    va.set_defaults(fn=cmd_valuation)
    la = sub.add_parser("land", help="land the SEC filings, Dolt's statements or its revenue consensus")
    la.add_argument("what", choices=("sec", "statements", "consensus", "all"))
    la.set_defaults(fn=cmd_land)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
