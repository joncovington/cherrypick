"""Item 46, the variance premium by underlying, tenor and strike (docs/edge-hypotheses-plan.md): its inputs.

    python scripts/variance_premium_item46.py plan [--out FILE]   # chain-path requests

`plan` writes the input of `fetch-chain-paths`: for every name, each standard monthly cycle from
`FIRST_EXPIRY` to `LAST_EXPIRY`, the expiry's daily end-of-day path from `PATH_DAYS` (66) calendar days
before it to the expiry itself, the convention the store already holds, so paths fetched for earlier
studies are skipped by the fetch rather than fetched again. It writes under the backtest data dir
(results/research/item46/), never into the repo, and prints counts only.

**Declared, as the plan states:** the equity-index group is SPX (its SPXW third-Friday expiry,
PM-settled), SPY, QQQ and IWM; the non-equity group is GLD, SLV and TLT. A monthly expiry is the third
Friday, or the trading day before it when that Friday is a holiday. The `coverage` and `study` steps land
in their own changes, before any P&L is read.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import calendar as cal

GROUPS = {
    "equity_index": ("SPXW", "SPY", "QQQ", "IWM"),
    "non_equity": ("GLD", "SLV", "TLT"),
}
NAMES = tuple(n for names in GROUPS.values() for n in names)
FIRST_EXPIRY = date(2016, 3, 18)
LAST_EXPIRY = date(2026, 10, 16)
PATH_DAYS = 66
TENORS = (60, 45, 30, 14, 7)
TIME_SPLIT = date(2021, 6, 1)


# ------------------------------------------------------------------------------------------------
# Pure.


def monthly_expiry(year: int, month: int) -> date:
    """The standard monthly: the third Friday, or the trading day before it when that is a holiday."""
    d = cal.nth_weekday(year, month, 4, 3)
    return d if cal.is_trading_day(d) else cal.previous_trading_day(d)


def cycles(first: date = FIRST_EXPIRY, last: date = LAST_EXPIRY) -> list[date]:
    out, y, m = [], first.year, first.month
    while (y, m) <= (last.year, last.month):
        e = monthly_expiry(y, m)
        if first <= e <= last:
            out.append(e)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def path_request(symbol: str, expiry: date) -> dict:
    return {
        "symbol": symbol,
        "expiration": expiry.isoformat(),
        "start": (expiry - timedelta(days=PATH_DAYS)).isoformat(),
        "through": expiry.isoformat(),
    }


def build_requests(names=NAMES, expiries=None) -> list[dict]:
    """Every name's every cycle, names in group order: pure."""
    expiries = cycles() if expiries is None else expiries
    return [path_request(sym, e) for sym in names for e in expiries]


def held(requests: list[dict], complete: set[tuple[str, str, str, str]]) -> int:
    """How many requests the store already holds complete, under the fetch's exact key."""
    return sum((r["symbol"], r["expiration"], r["start"], r["through"]) in complete for r in requests)


# ------------------------------------------------------------------------------------------------
# The store and the step.


def _out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item46"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _complete_paths() -> set[tuple[str, str, str, str]]:
    from cherrypick.backtest import chainfetch, settings

    path = chainfetch.db_path(settings.load(None))
    if not path.exists():
        return set()
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return {
            tuple(r)
            for r in conn.execute(
                "SELECT symbol, expiration, start, through FROM eod_chain_paths WHERE status = 'complete'"
            )
        }
    finally:
        conn.close()


def cmd_plan(args) -> int:
    reqs = build_requests()
    path = Path(args.out) if args.out else _out_dir() / "path_requests.json"
    path.write_text(json.dumps({"requests": reqs}, indent=0), encoding="utf-8")
    print(
        json.dumps(
            {
                "names": len(NAMES),
                "cycles": len(cycles()),
                "requests": len(reqs),
                "held_complete": held(reqs, _complete_paths()),
                "file": str(path),
            }
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("plan")
    sp.add_argument("--out")
    sp.set_defaults(fn=cmd_plan)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
