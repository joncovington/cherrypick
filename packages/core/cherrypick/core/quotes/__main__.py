"""`python -m cherrypick.core.quotes` -- a one-off quote and greek snapshot from a streamer instance.

    python -m cherrypick.core.quotes SPY .SPY270219P580 "SPY   270219P00560000"
    python -m cherrypick.core.quotes SPY --grid SPY 270115,270219 P 540:720:5

Options: --instance (default margin), --wait (seconds; default 180 in regular hours, 15 outside
them), --json (the whole result as JSON; the default is a table), --keep (leave the request file in
place), --rest-service (the keyring service whose session the REST fallback uses; default the
suite's shared login), --no-rest (no fallback: a leg the cache never fills stays missing).

The request file is removed when the snapshot is read. See the package docstring for what `usable`
means, why a weekend snapshot is the last close, and the REST fallback.
"""

from __future__ import annotations

import argparse
import json
import sys

from cherrypick.core import quotes

#: Outside regular hours the producer has nothing new to wait for: a leg it has not cached by now
#: goes to the REST fallback.
OFF_HOURS_WAIT_SECONDS = 15.0


def default_wait(now=None) -> float:
    from cherrypick.core import clock

    return quotes.DEFAULT_WAIT_SECONDS if clock.in_rth(now or clock.now_et()) else OFF_HOURS_WAIT_SECONDS


def rest_source(keyring_service: str):
    """`rest(occ_symbols) -> {occ: quote}` on a read-only adapter for `keyring_service`, any age.
    Fails closed to `{}` (the adapter's rule), so a broken login leaves the legs missing."""
    from cherrypick.core.live import account_alerts

    broker = account_alerts.make_broker(keyring_service)

    def rest(occs: list[str]) -> dict:
        return broker.rest_quotes(options=occs, max_age_seconds=float("inf"))["options"]

    return rest


def _fmt(v, spec: str) -> str:
    """`v` in `spec`, or a dash padded to the same width so a blank greek keeps its column."""
    if v is not None:
        return format(v, spec)
    width = int(spec.split(".")[0] or 0)
    return format("-", f">{width}")


def _note(row: dict) -> str:
    """The filter's reason, plus `rest` on a row the REST fallback supplied (it has no greeks)."""
    return " ".join(x for x in (row["reason"], "rest" if row.get("source") == "rest" else "") if x)


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
            f"{_fmt(r['age_seconds'], '9.0f')}  {_note(r)}"
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
    ap.add_argument("--wait", type=float, default=None, help="seconds (default 180 in RTH, 15 outside)")
    ap.add_argument("--keep", action="store_true", help="leave the request file in place")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--rest-service", default=None, help="keyring service for the REST fallback")
    ap.add_argument("--no-rest", action="store_true", help="no REST fallback for unfilled legs")
    args = ap.parse_args(argv)

    syms = list(args.symbols)
    for root, exps, right, strikes in args.grid or []:
        syms += quotes.grid(root, exps.split(","), right, quotes.strike_range(strikes))
    if not syms:
        ap.error("give symbols or --grid")
    rest = None
    if not args.no_rest:
        from cherrypick.core.auth import SHARED_SERVICE

        rest = rest_source(args.rest_service or SHARED_SERVICE)
    wait = args.wait if args.wait is not None else default_wait()
    result = quotes.snapshot(
        syms, instance=args.instance, wait_seconds=wait, keep_request=args.keep, rest=rest
    )
    print(json.dumps(result, indent=1, default=str) if args.json else table(result))
    return 0 if not result["missing"] else 1


if __name__ == "__main__":
    sys.exit(main())
