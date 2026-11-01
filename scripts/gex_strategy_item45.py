"""Item 45, a published GEX share strategy out of sample (docs/edge-hypotheses-plan.md): its inputs.

    python scripts/gex_strategy_item45.py plan [--cell pilot|P|N] [--out FILE]      # open-interest plan
    python scripts/gex_strategy_item45.py requests [--cell pilot|P|N] [--out FILE]  # chain requests

`plan` writes the input of `fetch-oi`: every session of the cell for its names, plus the probe's
sessions for the pilot. `requests` reads the open-interest store for each (name, session)'s listed
expiries and writes the input of `fetch-intraday-chains`. Both write under the backtest data dir
(results/research/item45/), never into the repo: the inputs are licensed. Counts only are printed.

**What is asked for, per (name, session), as declared:**

- *Wide:* every expiry from the session through `GAUGE_DAYS` (60) calendar days, the whole chain, at
  `WIDE_INTERVAL` from 09:30 to 15:45 ET. The gauge, the walls and net GEX are read from it.
- *Minute samples:* one expiry beyond the gauge's reach (the nearest listed `SAMPLE_DAYS[0]` to
  `SAMPLE_DAYS[1]` days out), one strike either side of the money, at `SAMPLE_INTERVAL` from 09:30 to
  16:00. Only its underlying price is read: the stock bars are built from it. It is beyond 60 days so
  it never shares bars with a wide request (the fetch keeps one strike range per expiry's bars).
- *The probe* (pilot only): `PROBE_NAME` on `PROBE_SESSIONS`, wide as above and samples at both one
  and five minutes, to measure what the vendor serves for a single stock before 2026.

A (name, session) whose open-interest day is missing or not complete has no listed expiries, so it
gets no request and is counted. The stock bars' basis (1- or 5-minute samples) is the pilot's
decision against the broker's 15-minute candles; the `bars`, `pilot` and `study` steps land in their
own changes, before any of the rule's output is read.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import calendar as cal

PILOT_NAMES = ("AAPL", "JPM", "KO")
CELL_P_NAMES = ("AAPL", "GOOG", "MSFT", "JPM", "WMT", "HD", "DIS", "KO", "CAT", "MS", "MCD", "COST")
CELL_N_NAMES = ("NEE", "JNJ", "BAC", "CCJ", "V", "PFE", "UNH", "PEP", "C", "XOM")
CELLS = {
    "pilot": (PILOT_NAMES, date(2026, 4, 13), date(2026, 10, 30)),
    "P": (CELL_P_NAMES, date(2026, 4, 13), date(2026, 10, 30)),
    "N": (CELL_N_NAMES, date(2018, 1, 16), date(2026, 4, 10)),
}
PROBE_NAME = "NEE"
PROBE_SESSIONS = (
    date(2018, 3, 15),
    date(2019, 9, 17),
    date(2021, 6, 15),
    date(2023, 2, 14),
    date(2025, 5, 13),
)
GAUGE_DAYS = 60
SAMPLE_DAYS = (61, 100)
WIDE_INTERVAL = "15m"
WIDE_WINDOW = ("09:30", "15:45")
SAMPLE_INTERVAL = "1m"
PROBE_SAMPLE_INTERVALS = ("1m", "5m")
SAMPLE_WINDOW = ("09:30", "16:00")
SAMPLE_STRIKES = 1


# ------------------------------------------------------------------------------------------------
# Pure.


def sessions(a: date, b: date) -> list[date]:
    d = a if cal.is_trading_day(a) else cal.next_trading_day(a)
    out = []
    while d <= b:
        out.append(d)
        d = cal.next_trading_day(d)
    return out


def cell_days(cell: str) -> dict[str, list[str]]:
    """{session: [names]} for a cell; the pilot adds the probe's sessions for its one name."""
    names, a, b = CELLS[cell]
    out = {d.isoformat(): list(names) for d in sessions(a, b)}
    if cell == "pilot":
        for d in PROBE_SESSIONS:
            out.setdefault(d.isoformat(), []).append(PROBE_NAME)
    return dict(sorted(out.items()))


def wide_expiries(session: str, expiries: list[str]) -> list[str]:
    last = (date.fromisoformat(session) + timedelta(days=GAUGE_DAYS)).isoformat()
    return sorted(e for e in set(expiries) if session <= e <= last)


def sample_expiry(session: str, expiries: list[str]) -> str | None:
    d = date.fromisoformat(session)
    lo, hi = ((d + timedelta(days=n)).isoformat() for n in SAMPLE_DAYS)
    near = sorted(e for e in set(expiries) if lo <= e <= hi)
    return near[0] if near else None


def requests_for(symbol: str, session: str, expiries: list[str], sample_intervals) -> list[dict]:
    """One (name, session)'s requests: pure."""
    out = [
        {
            "symbol": symbol,
            "session": session,
            "expiration": e,
            "start": WIDE_WINDOW[0],
            "end": WIDE_WINDOW[1],
            "interval": WIDE_INTERVAL,
        }
        for e in wide_expiries(session, expiries)
    ]
    far = sample_expiry(session, expiries)
    if far is not None:
        out += [
            {
                "symbol": symbol,
                "session": session,
                "expiration": far,
                "start": SAMPLE_WINDOW[0],
                "end": SAMPLE_WINDOW[1],
                "interval": iv,
                "strike_range": SAMPLE_STRIKES,
            }
            for iv in sample_intervals
        ]
    return out


def build_requests(days: dict[str, list[str]], listed: dict[tuple[str, str], list[str]]) -> dict:
    """Every request for a cell from the expiries the open-interest store lists: pure. A (name, session)
    with nothing listed is counted, not guessed."""
    reqs, unlisted, no_sample = [], 0, 0
    for session, names in days.items():
        for sym in names:
            exps = listed.get((sym, session))
            if not exps:
                unlisted += 1
                continue
            probe = sym == PROBE_NAME and date.fromisoformat(session) in PROBE_SESSIONS
            got = requests_for(sym, session, exps, PROBE_SAMPLE_INTERVALS if probe else (SAMPLE_INTERVAL,))
            if not any("strike_range" in r for r in got):
                no_sample += 1
            reqs += got
    return {"requests": reqs, "unlisted": unlisted, "no_sample_expiry": no_sample}


# ------------------------------------------------------------------------------------------------
# The stores and the steps.


def _out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item45"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _oi_store() -> sqlite3.Connection:
    from cherrypick.backtest import oifetch, settings

    path = oifetch.db_path(settings.load(None))
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def listed_expiries(conn, days: dict[str, list[str]]) -> dict[tuple[str, str], list[str]]:
    out: dict[tuple[str, str], list[str]] = {}
    for session, names in days.items():
        for sym in names:
            done = conn.execute(
                "SELECT status FROM oi_days WHERE symbol = ? AND session = ?", (sym, session)
            ).fetchone()
            if not done or done[0] != "complete":
                continue
            out[(sym, session)] = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT expiration FROM oi_contracts WHERE symbol = ? AND session = ?",
                    (sym, session),
                )
            ]
    return out


def cmd_plan(args) -> int:
    days = cell_days(args.cell)
    path = Path(args.out) if args.out else _out_dir() / f"oi_plan_{args.cell}.json"
    path.write_text(json.dumps({"sessions": days}, indent=0), encoding="utf-8")
    print(
        json.dumps(
            {
                "cell": args.cell,
                "sessions": len(days),
                "name_days": sum(map(len, days.values())),
                "file": str(path),
            }
        )
    )
    return 0


def cmd_requests(args) -> int:
    days = cell_days(args.cell)
    built = build_requests(days, listed_expiries(_oi_store(), days))
    path = Path(args.out) if args.out else _out_dir() / f"chain_requests_{args.cell}.json"
    path.write_text(json.dumps({"requests": built["requests"]}, indent=0), encoding="utf-8")
    print(
        json.dumps(
            {
                "cell": args.cell,
                "requests": len(built["requests"]),
                "unlisted_name_days": built["unlisted"],
                "no_sample_expiry": built["no_sample_expiry"],
                "file": str(path),
            }
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("plan", cmd_plan), ("requests", cmd_requests)):
        sp = sub.add_parser(name)
        sp.add_argument("--cell", choices=sorted(CELLS), default="pilot")
        sp.add_argument("--out")
        sp.set_defaults(fn=fn)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
