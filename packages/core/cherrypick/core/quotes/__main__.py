"""`python -m cherrypick.core.quotes` -- a one-off quote and greek snapshot from a streamer instance.

    python -m cherrypick.core.quotes SPY .SPY270219P580 "SPY   270219P00560000"
    python -m cherrypick.core.quotes SPY --grid SPY 270115,270219 P 540:720:5

Options: --instance (default margin), --wait (seconds, default 180), --json (the whole result as
JSON; the default is a table), --keep (leave the request file in place).

The request file is removed when the snapshot is read. See the package docstring for what `usable`
means and why a weekend snapshot is the last close.
"""

from __future__ import annotations

import argparse
import json
import sys

from cherrypick.core import quotes


def _fmt(v, spec: str) -> str:
    return "-" if v is None else format(v, spec)


def table(result: dict) -> str:
    lines = [
        f"instance {result['instance']}  requested {result['requested']}  missing {len(result['missing'])}",
    ]
    header = (
        f"{'symbol':<22}{'bid':>9}{'ask':>9}{'mid':>9}{'width':>7}{'delta':>7}{'iv':>7}{'age s':>9}  note"
    )
    lines.append(header)
    for sym, r in sorted(result["rows"].items()):
        if "usable" not in r:
            px = r["last"] if r["last"] is not None else r["mid"]
            blank = " " * 18
            age = _fmt(r["age_seconds"], "9.0f")
            lines.append(f"{sym:<22}{blank}{_fmt(px, '9.2f')}{' ' * 21}{age}  underlying")
            continue
        iv = r["iv"] * 100 if r["iv"] is not None else None
        lines.append(
            f"{sym:<22}{_fmt(r['bid'], '9.2f')}{_fmt(r['ask'], '9.2f')}{_fmt(r['mid'], '9.3f')}"
            f"{_fmt(r['width'], '7.2f')}{_fmt(r['delta'], '7.2f')}{_fmt(iv, '7.1f')}"
            f"{_fmt(r['age_seconds'], '9.0f')}  {r['reason']}"
        )
    for sym in result["missing"]:
        lines.append(f"{sym:<22}  missing: the cache never filled it")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cherrypick.core.quotes", description=__doc__.splitlines()[0])
    ap.add_argument("symbols", nargs="*", help="OCC, streamer or underlying symbols")
    ap.add_argument(
        "--grid",
        nargs=4,
        action="append",
        metavar=("ROOT", "EXPIRATIONS", "RIGHT", "LO:HI:STEP"),
        help="a strike ladder, e.g. SPY 270115,270219 P 540:720:5 (repeatable)",
    )
    ap.add_argument("--instance", default=quotes.DEFAULT_INSTANCE)
    ap.add_argument("--wait", type=float, default=quotes.DEFAULT_WAIT_SECONDS)
    ap.add_argument("--keep", action="store_true", help="leave the request file in place")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    syms = list(args.symbols)
    for root, exps, right, strikes in args.grid or []:
        syms += quotes.grid(root, exps.split(","), right, quotes.strike_range(strikes))
    if not syms:
        ap.error("give symbols or --grid")
    result = quotes.snapshot(syms, instance=args.instance, wait_seconds=args.wait, keep_request=args.keep)
    print(json.dumps(result, indent=1, default=str) if args.json else table(result))
    return 0 if not result["missing"] else 1


if __name__ == "__main__":
    sys.exit(main())
