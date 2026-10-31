"""SPXW weekly put minute greeks for each bwb position's whole life, plus the ATM calls at 10:00 for the
expected move. Study store: <vendor_dir>/addon_weeklies.db (licensed dir; packages/backtest `stores.STORES`).

    python scripts/fetch_addon_weeklies.py requests [--start D] [--end D] [--out FILE]
    python scripts/fetch_addon_weeklies.py fetch [START END]        # the old Friday-only run

`requests` works out which (expiry, session) pairs the module's ladder needs and the store lacks, and
writes the input of `python -m cherrypick.backtest fetch-addon-weeklies` (the research queue's
`fetch-addon-weeklies` kind). It reads the store's presence, never a price. A pair is needed for every
trading day the ladder enters an expiry (`clock.target_expiration`, as the module computes it) and every
day after, through the expiry's own: a Friday's first entry is the Friday before (7 days), but the next
Friday after a holiday-shifted Thursday is also entered from that Thursday (8 days), and a Thursday
expiry (the Friday was a holiday) is a weekly of its own. The store was first built for Fridays only
(2026-10-23), so those two cases are what `requests` finds. An expiry the vendor refused before
(`fetched.status = 'refused'`, sub-minute history not served for that date) is not asked for again
unless `--retry-refused`; its entries are counted as unfetchable.

`fetch` is the old command, kept so the store can be rebuilt: every Friday expiry the vendor lists in
START..END (default 2022-05-20 2026-10-16), each from the Friday before. It holds the vendor session,
so queue the `requests` file through the research queue instead, or run it outside the backtest fetch
window.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

START = "2022-05-13"  # the first entry day: the ladder's entry into the first stored expiry
END = "2026-10-16"  # the last expiry the store reaches


def sessions(a: date, b: date) -> list[date]:
    """Trading days from a through b."""
    from cherrypick.core import calendar as cal

    out, d = [], a
    while d <= b:
        if cal.is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def entry_pairs(a: date, b: date, last_expiry: date) -> list[tuple[date, date]]:
    """(entry day, expiry) for each trading day in a..b whose expiry the module's clock puts on or before
    `last_expiry`."""
    from cherrypick.bwb import clock

    out = []
    for d in sessions(a, b):
        plan = clock.target_expiration(d)
        if plan is not None and date.fromisoformat(plan["expiration"]) <= last_expiry:
            out.append((d, date.fromisoformat(plan["expiration"])))
    return out


def lives(pairs: list[tuple[date, date]]) -> dict[date, list[date]]:
    """Each expiry's sessions: its first entry day through the expiry itself, trading days only."""
    first: dict[date, date] = {}
    for entry, exp in pairs:
        first[exp] = min(entry, first.get(exp, entry))
    return {exp: sessions(f, exp) for exp, f in sorted(first.items())}


def runs(days: list[date], missing: set[date]) -> list[tuple[date, date]]:
    """The missing sessions as runs of consecutive sessions of `days`: [(first, last)]."""
    out: list[list[date]] = []
    prev_missing = False
    for d in days:
        if d in missing:
            if prev_missing:
                out[-1][1] = d
            else:
                out.append([d, d])
        prev_missing = d in missing
    return [(a, b) for a, b in out]


def needed_spans(
    life: dict[date, list[date]], held: dict[str, set[str]], refused: set[str]
) -> tuple[list[dict], dict]:
    """The requests the store still needs, and the counts behind them. `held` is {expiry: sessions the
    store has rows for}; `refused` the expiries the vendor will not serve. Pure."""
    spans, counts = [], {"expiries_new": 0, "expiries_topped_up": 0, "sessions": 0, "refused_expiries": 0}
    for exp, days in life.items():
        have = held.get(exp.isoformat(), set())
        if exp.isoformat() in refused and not have:
            counts["refused_expiries"] += 1
            continue
        missing = {d for d in days if d.isoformat() not in have}
        if not missing:
            continue
        counts["expiries_new" if not have else "expiries_topped_up"] += 1
        counts["sessions"] += len(missing)
        spans += [
            {"expiration": exp.isoformat(), "first": a.isoformat(), "last": b.isoformat()}
            for a, b in runs(days, missing)
        ]
    return spans, counts


def store_state(path: Path) -> tuple[dict[str, set[str]], set[str]]:
    """({expiry: sessions held}, expiries the vendor refused), read-only; empty when there is no store."""
    if not path.exists():
        return {}, set()
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        held: dict[str, set[str]] = {}
        for exp, day in conn.execute("SELECT DISTINCT expiration, day FROM q"):
            held.setdefault(exp, set()).add(day)
        refused = {r[0] for r in conn.execute("SELECT expiration FROM fetched WHERE status = 'refused'")}
        return held, refused
    finally:
        conn.close()


def build_requests(start: date, end: date, held, refused, *, retry_refused: bool = False) -> dict:
    """The request file's body and the counts, from the calendar and what the store holds."""
    pairs = entry_pairs(start, end, end)
    spans, counts = needed_spans(lives(pairs), held, set() if retry_refused else refused)
    unfetchable = sum(1 for _, exp in pairs if exp.isoformat() in refused and not held.get(exp.isoformat()))
    return {
        "requests": spans,
        "counts": {
            **counts,
            "requests": len(spans),
            "vendor_calls": 2 * len(spans),
            "entries": len(pairs),
            "entries_unfetchable": 0 if retry_refused else unfetchable,
        },
    }


def cmd_requests(args) -> int:
    from cherrypick.backtest import addonfetch, settings

    held, refused = store_state(addonfetch.db_path(settings.load(None)))
    body = build_requests(
        date.fromisoformat(args.start),
        date.fromisoformat(args.end),
        held,
        refused,
        retry_refused=args.retry_refused,
    )
    if args.out:
        out = Path(args.out)
    else:
        from cherrypick.backtest import paths

        out = paths.results_dir() / "research" / "item35" / "addon_requests.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"requests": body["requests"]}, indent=1), encoding="utf-8")
    print(json.dumps({**body["counts"], "file": str(out)}))
    return 0


def cmd_fetch(args) -> int:
    """The old run: every Friday expiry the vendor lists in START..END, from the Friday before."""
    from cherrypick.backtest import addonfetch, settings
    from cherrypick.backtest.vendor import client_for

    s = settings.load(None)
    client = client_for(s)
    try:
        exps = [
            e
            for e in client.expirations("SPXW")
            if args.begin <= e <= args.finish and date.fromisoformat(e).weekday() == 4
        ]
        spans = [
            (e, (date.fromisoformat(e) - timedelta(days=addonfetch.LEGACY_LEAD_DAYS)).isoformat(), e)
            for e in exps
        ]
        print(json.dumps(addonfetch.fetch_spans(s, client, spans, log=lambda m: print(m, flush=True))))
    finally:
        client.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    rq = sub.add_parser("requests", help="the request file for the sessions the store lacks")
    rq.add_argument("--start", default=START, help="first entry day")
    rq.add_argument("--end", default=END, help="last expiry")
    rq.add_argument("--out")
    rq.add_argument("--retry-refused", action="store_true", help="ask again for expiries the vendor refused")
    rq.set_defaults(fn=cmd_requests)
    fe = sub.add_parser("fetch", help="the old Friday-only run (holds the vendor session)")
    fe.add_argument("begin", nargs="?", default="2022-05-20")
    fe.add_argument("finish", nargs="?", default=END)
    fe.set_defaults(fn=cmd_fetch)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
