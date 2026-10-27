"""Fetch the SPXW quotes item 20 (the Convex Stack) needs, into the licensed store.

    python scripts/fetch_spx_convex_stack.py 2022-05-16 2026-10-23

For each entry day D in the range (a Monday or a Thursday that is a trading day), with X its expiry
(`expiry_for`: the Friday after a Monday, the Tuesday after a Thursday, moved to the trading day
before when that day is a holiday):

- `entry`: the SPXW chain expiring X, D's one-minute bars 15:40-15:50 (15:45 is the test's entry;
  15:40 and 15:50 are its declared neighbours);
- `path`:  the same expiry on every session after D through X, 09:30-16:00 at five minutes, one
  request per session.

Builds `SPX-convex-stack.db` in the backtest vendor directory (packages/backtest `stores.STORES`).
Resumable: a (day, expiration, leg, session) already recorded (with rows, or as refused) is skipped.
Writes only under the backtest vendor directory (marked licensed). Prints progress counts only.
"""

import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from cherrypick.backtest import paths
from cherrypick.backtest import settings as _settings
from cherrypick.backtest.vendor import VendorRefusal, client_for
from cherrypick.core import calendar as _calendar

DB_NAME = "SPX-convex-stack.db"
#: Strikes either side of spot. SPXW lists at 5 points, so about 400 points: a 50-wide fly about
#: 100 points out, plus a week's move, stays inside it.
STRIKE_RANGE = 80
WORKERS = 4
ENTRY = ("1m", "15:40:00", "15:50:00")
PATH = ("5m", "09:30:00", "16:00:00")
#: Entry weekday -> days to the target expiry (Monday -> Friday, Thursday -> next Tuesday).
TARGET_DAYS = {0: 4, 3: 5}


def expiry_for(d: date) -> date:
    """The listed expiry for an entry on `d`: the target day, or the trading day before it."""
    x = d + timedelta(days=TARGET_DAYS[d.weekday()])
    while not _calendar.is_trading_day(x):
        x -= timedelta(days=1)
    return x


def sessions_after(d: date, x: date) -> list[date]:
    out, s = [], d + timedelta(days=1)
    while s <= x:
        if _calendar.is_trading_day(s):
            out.append(s)
        s += timedelta(days=1)
    return out


def plan(start: str, end: str, done: set) -> list[tuple[date, date, str, date]]:
    """(entry day, expiry, leg, the session the request reads), not yet recorded."""
    todo = []
    d, last = date.fromisoformat(start), date.fromisoformat(end)
    while d <= last:
        if d.weekday() in TARGET_DAYS and _calendar.is_trading_day(d):
            x = expiry_for(d)
            reqs = [(d, x, "entry", d)] + [(d, x, "path", s) for s in sessions_after(d, x)]
            todo += [
                r for r in reqs if tuple(v.isoformat() if isinstance(v, date) else v for v in r) not in done
            ]
        d += timedelta(days=1)
    return todo


def main(start, end):
    cfg = _settings.load()
    db = sqlite3.connect(paths.ensure_vendor_dir(cfg) / DB_NAME)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS quotes (day TEXT, expiration TEXT, leg TEXT, ts TEXT, strike REAL,
            right TEXT, bid REAL, ask REAL, delta REAL, iv REAL, underlying REAL,
            PRIMARY KEY (day, expiration, leg, ts, strike, right));
        CREATE TABLE IF NOT EXISTS fetched (day TEXT, expiration TEXT, leg TEXT, session TEXT,
            status TEXT, rows INTEGER, PRIMARY KEY (day, expiration, leg, session));
        """
    )
    done = {tuple(r) for r in db.execute("SELECT day, expiration, leg, session FROM fetched")}
    todo = plan(start, end, done)
    print(f"to fetch: {len(todo)} (entry, expiry, leg, session) requests", flush=True)
    client = client_for(cfg)

    def one(req):
        d, x, leg, s = req
        interval, t0, t1 = ENTRY if leg == "entry" else PATH
        try:
            df = client._call(
                "option_history_greeks_first_order",
                symbol="SPXW",
                expiration=x,
                date=s,
                interval=interval,
                strike_range=STRIKE_RANGE,
                start_time=t0,
                end_time=t1,
            )
        except VendorRefusal as refusal:
            return req, refusal.reason, []
        rows = []
        for r in df.itertuples(index=False):
            rows.append(
                (
                    r.timestamp.isoformat(),
                    float(r.strike),
                    str(r.right).upper()[0],
                    _f(r.bid),
                    _f(r.ask),
                    _f(r.delta),
                    _f(r.implied_vol),
                    _f(r.underlying_price),
                )
            )
        return req, "ok", rows

    n_req, n_rows, t0 = 0, 0, time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for (d, x, leg, s), status, rows in pool.map(one, todo):
                key = (d.isoformat(), x.isoformat(), leg)
                if status in ("unauthenticated", "session_in_use"):
                    raise SystemExit(f"stopped at {key} {s}: {status}")
                db.executemany(
                    "INSERT OR REPLACE INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [(*key, *row) for row in rows],
                )
                db.execute(
                    "INSERT OR REPLACE INTO fetched VALUES (?,?,?,?,?,?)",
                    (*key, s.isoformat(), status, len(rows)),
                )
                db.commit()
                n_req += 1
                n_rows += len(rows)
                if n_req % 100 == 0:
                    rate = n_req / (time.monotonic() - t0)
                    print(f"{key[0]} requests={n_req}/{len(todo)} rows={n_rows} {rate:.2f}/s", flush=True)
    finally:
        client.close()
        db.close()
    print(f"done through {end}: requests={n_req} rows={n_rows}", flush=True)


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
