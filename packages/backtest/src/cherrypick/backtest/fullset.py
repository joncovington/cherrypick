"""The full option data set for seven index and fund underlyings: what to ask the vendor for, as
request files the existing fetch verbs read. Pure: no vendor call, no file write (the script
`packages/backtest/scripts/fetch_full_set.py` writes what this returns and queues the fetches).

**The underlyings** are SPX, QQQ, XSP, SPY, IWM, GLD and TLT, in the order they are fetched. **The
span** starts at `START` (2020-01-01, the furthest the Value tier reaches) and runs through the day
the plan is made, so planning again later extends it and a re-run fetches only what is new.

**The four parts**, each in stores of its own so no study store changes under it:

- **Same-day expiries**, every session at 60 s with 40 strikes each side: the `full` store tag,
  `<SYM>-60s-full.db`, fetched by `fetch --store-tag full` (the research queue's `fetch-sessions`).
- **Near expiries** (1 to `NEAR_DAYS` calendar days out), each session 09:30 to the close at one
  minute: `<SYM>-near-1m.db`, by `fetch-intraday-chains`.
- **Middle expiries** (`MID_DAYS`), the standard monthlies and the weeklies out to `MID_WEEKLY_DAYS`,
  each session at five minutes: `<SYM>-mid-5m.db`, by `fetch-intraday-chains`.
- **End-of-day chains**, every strike: each weekly, monthly and quarter-end expiry's daily path over
  its life inside the span (monthlies reach back `MONTHLY_PATH_DAYS`, which takes in the LEAPS), by
  `fetch-chain-paths` into `eod_chains.db`; and open interest for every session, by `fetch-oi`.

**Expiries are candidates, not a listing.** Which expiries existed on a past day is not asked of the
vendor here (a listing call would take the vendor session from a running fetch). The near part asks
for every trading day in its range, the others for every date the exchanges' rules could list; the
vendor answers an expiry that was never listed with nothing, which the fetch records as `empty` and
never asks again. So a candidate that did not exist costs one short request.

**Strikes** are counted either side of the money (`strike_range`): enough for a 1-7 day short strike
and its wing at each fund's strike spacing, wider for the middle expiries. The end-of-day chains are
whole.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from cherrypick.core import calendar as cal

START = "2020-01-01"
#: Fetch order: the most asked-for first.
SYMBOLS = ("SPX", "QQQ", "XSP", "SPY", "IWM", "GLD", "TLT")

#: Per underlying: the vendor root of its PM-settled options (the intraday parts), the roots its
#: end-of-day chains are stored under, and strikes either side of the money for the near and middle
#: parts. SPX's weeklies and dailies are SPXW; its AM-settled monthlies are SPX.
SPEC: dict[str, dict[str, Any]] = {
    "SPX": {"root": "SPXW", "eod": ("SPXW", "SPX"), "near_strikes": 60, "mid_strikes": 120},
    "QQQ": {"root": "QQQ", "eod": ("QQQ",), "near_strikes": 30, "mid_strikes": 60},
    "XSP": {"root": "XSP", "eod": ("XSP",), "near_strikes": 30, "mid_strikes": 60},
    "SPY": {"root": "SPY", "eod": ("SPY",), "near_strikes": 30, "mid_strikes": 60},
    "IWM": {"root": "IWM", "eod": ("IWM",), "near_strikes": 20, "mid_strikes": 30},
    "GLD": {"root": "GLD", "eod": ("GLD",), "near_strikes": 30, "mid_strikes": 50},
    "TLT": {"root": "TLT", "eod": ("TLT",), "near_strikes": 15, "mid_strikes": 20},
}

NEAR_DAYS = 7
NEAR_INTERVAL = "1m"
MID_DAYS = (8, 90)
MID_WEEKLY_DAYS = 45
MID_INTERVAL = "5m"
#: How far before its expiry each end-of-day path starts (clipped to the span's start).
WEEKLY_PATH_DAYS = 60
QUARTERLY_PATH_DAYS = 400
MONTHLY_PATH_DAYS = 1100
#: How far past the plan day the end-of-day part looks for listed expiries (LEAPS).
LISTED_AHEAD_DAYS = 1100
OPEN = "09:30"


def zero_store(symbol: str) -> str:
    return f"{symbol}-60s-full.db"


def near_store(symbol: str) -> str:
    return f"{symbol}-near-1m.db"


def mid_store(symbol: str) -> str:
    return f"{symbol}-mid-5m.db"


#: Every intraday store this set writes: `intradayfetch` accepts a request file naming one of these.
INTRADAY_STORES = tuple(f(s) for s in SYMBOLS for f in (near_store, mid_store))


def sessions(start: str, through: str) -> list[date]:
    """The trading days from `start` through `through`."""
    d, last, out = date.fromisoformat(start), date.fromisoformat(through), []
    while d <= last:
        if cal.is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def weekly_expiry(friday: date) -> date:
    """A week's expiry: the Friday, or the trading day before it when the Friday is a holiday."""
    d = friday
    while not cal.is_trading_day(d):
        d -= timedelta(days=1)
    return d


def weeklies(start: date, through: date) -> list[date]:
    """Each week's expiry from `start` through `through`."""
    d = start + timedelta(days=(4 - start.weekday()) % 7)
    out = []
    while d <= through + timedelta(days=6):
        e = weekly_expiry(d)
        if start <= e <= through:
            out.append(e)
        d += timedelta(days=7)
    return out


def monthlies(start: date, through: date) -> list[date]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (through.year, through.month):
        e = cal.monthly_expiry(y, m)
        if start <= e <= through:
            out.append(e)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def quarter_ends(start: date, through: date) -> list[date]:
    return [e for y in range(start.year, through.year + 1) for e in cal.quarterly_expiry_dates(y)
            if start <= e <= through]  # fmt: skip


def _window(day: date) -> tuple[str, str]:
    return OPEN, cal.session_close_hhmm(day)


def near_requests(symbol: str, start: str, through: str) -> dict:
    """Every session's candidate expiries 1 to `NEAR_DAYS` calendar days out, open to close at one
    minute, for `near_store(symbol)`."""
    spec, out = SPEC[symbol], []
    for d in sessions(start, through):
        lo, hi = _window(d)
        for k in range(1, NEAR_DAYS + 1):
            e = d + timedelta(days=k)
            if cal.is_trading_day(e):
                out.append({"symbol": spec["root"], "session": d.isoformat(), "expiration": e.isoformat(),
                            "start": lo, "end": hi, "interval": NEAR_INTERVAL,
                            "strike_range": spec["near_strikes"]})  # fmt: skip
    return {"store": near_store(symbol), "requests": out}


def mid_expiries(day: date) -> list[date]:
    """A session's middle expiries: the monthlies `MID_DAYS` out and the weeklies to `MID_WEEKLY_DAYS`."""
    lo, hi = day + timedelta(days=MID_DAYS[0]), day + timedelta(days=MID_DAYS[1])
    weekly = weeklies(lo, min(hi, day + timedelta(days=MID_WEEKLY_DAYS)))
    return sorted(set(weekly) | set(monthlies(lo, hi)))


def mid_requests(symbol: str, start: str, through: str) -> dict:
    """Every session's middle expiries, open to close at five minutes, for `mid_store(symbol)`."""
    spec, out = SPEC[symbol], []
    for d in sessions(start, through):
        lo, hi = _window(d)
        for e in mid_expiries(d):
            out.append({"symbol": spec["root"], "session": d.isoformat(), "expiration": e.isoformat(),
                        "start": lo, "end": hi, "interval": MID_INTERVAL,
                        "strike_range": spec["mid_strikes"]})  # fmt: skip
    return {"store": mid_store(symbol), "requests": out}


def path_requests(start: str, through: str) -> dict:
    """Each candidate expiry's end-of-day path inside the span, for every end-of-day root: weeklies
    from `WEEKLY_PATH_DAYS` before, quarter-ends from `QUARTERLY_PATH_DAYS`, monthlies (and so the
    LEAPS, which are monthlies) from `MONTHLY_PATH_DAYS`. An expiry still to come runs through the
    span's end; planning again later asks for the rest."""
    first, last = date.fromisoformat(start), date.fromisoformat(through)
    while not cal.is_trading_day(last):
        last -= timedelta(days=1)
    horizon = last + timedelta(days=LISTED_AHEAD_DAYS)
    lead: dict[date, int] = {}
    for e in weeklies(first, horizon):
        lead[e] = max(lead.get(e, 0), WEEKLY_PATH_DAYS)
    for e in quarter_ends(first, horizon):
        lead[e] = max(lead.get(e, 0), QUARTERLY_PATH_DAYS)
    for e in monthlies(first, horizon):
        lead[e] = max(lead.get(e, 0), MONTHLY_PATH_DAYS)
    out = []
    for sym in SYMBOLS:
        for root in SPEC[sym]["eod"]:
            for e, days in sorted(lead.items()):
                begin = max(first, e - timedelta(days=days))
                end = min(e, last)
                if begin > end:
                    continue
                out.append({"symbol": root, "expiration": e.isoformat(), "start": begin.isoformat(),
                            "through": end.isoformat()})  # fmt: skip
    return {"requests": out}


def oi_plan(start: str, through: str) -> dict:
    """Open interest for every underlying's root on every session (`fetch-oi`'s plan)."""
    roots = [SPEC[s]["root"] for s in SYMBOLS]
    return {"sessions": {d.isoformat(): roots for d in sessions(start, through)}}


def sample(doc: dict, n: int) -> dict:
    """The first `n` requests of a request file, the same store: the probe a chain runs first."""
    return {**doc, "requests": doc["requests"][:n]}


#: The probe: this many of SPX's near requests, fetched before anything else, so a tier or
#: entitlement the set cannot use stops the chain at its first row.
SAMPLE_REQUESTS = 10


def plan(start: str, through: str) -> dict[str, dict]:
    """Every file the chain reads, by name."""
    files: dict[str, dict] = {}
    for sym in SYMBOLS:
        files[f"near_{sym}.json"] = near_requests(sym, start, through)
        files[f"mid_{sym}.json"] = mid_requests(sym, start, through)
    files["sample.json"] = sample(files["near_SPX.json"], SAMPLE_REQUESTS)
    files["paths.json"] = path_requests(start, through)
    files["oi_plan.json"] = oi_plan(start, through)
    return files
