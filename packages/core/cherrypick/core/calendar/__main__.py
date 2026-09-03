"""`python -m cherrypick.core.calendar` — the suite's SHARED NYSE-holiday CLI.

The one calendar answer a read surface may need without owning a module's own bridge: `nyse_holidays`
already lives here as the suite's single source of trading days (see the package docstring), but until
now every caller needing it read Python directly. A read-only TypeScript surface (the console) cannot,
so this wraps the existing pure function for a subprocess bridge exactly the way `cherrypick.core.auth`
already is one — no new calendar logic, just a JSON-in/JSON-out shape over what `nyse_holidays` already
computes.

Commands:
    holidays --year Y [Y...]    {year: [iso dates]} for each requested year
"""

from __future__ import annotations

import argparse
import json
import sys

from . import nyse_holidays


def cmd_holidays(args) -> dict:
    return {
        "ok": True,
        "holidays": {str(year): sorted(d.isoformat() for d in nyse_holidays(year)) for year in args.year},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cherrypick.core.calendar", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    hd = sub.add_parser("holidays")
    hd.add_argument("--year", type=int, nargs="+", required=True)
    args = ap.parse_args(argv)
    fn = {"holidays": cmd_holidays}[args.cmd]
    result = fn(args)
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
