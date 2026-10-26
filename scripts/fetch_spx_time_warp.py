"""Fetch the SPXW quotes item 19 (the Time Warp) needs, into the licensed store.

    python scripts/fetch_spx_time_warp.py 2022-05-16 2026-10-23

For each trading day D in the range, with E the next trading day (a Friday's E is the Monday):

- `entry`: the SPXW chain expiring E, D's one-minute bars 15:45-15:55 (15:50 is the test's entry;
  15:45 and 15:55 are its declared neighbours);
- `path`:  the same expiry through E's session, 09:30-16:00 at five minutes.

The 0DTE store (`SPX-60s.db`) holds E's session too, but only about 100 points either side of
spot, and a fly bought the evening before can open the next morning further out than that after a
gap. So the path is fetched here at the entry's strike reach.

Builds `SPX-time-warp.db` in the backtest vendor directory (packages/backtest `stores.STORES`).
Resumable: a (day, expiration, leg) already recorded (with rows, or as refused) is skipped. Writes
only under the backtest vendor directory (marked licensed). Prints progress counts only.
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

DB_NAME = "SPX-time-warp.db"
#: Strikes either side of spot. SPXW lists at 5 points, so about 300 points: a 50-wide fly pushed out
#: to 5% of its width in a high-VIX evening, plus a gap, stays inside it.
STRIKE_RANGE = 60
WORKERS = 4
LEGS = {
    # leg: (which day's session, interval, start, end)
    "entry": ("D", "1m", "15:45:00", "15:55:00"),
    "path": ("E", "5m", "09:30:00", "16:00:00"),
}


def next_trading_day(d: date) -> date:
    e = d + timedelta(days=1)
    while not _calendar.is_trading_day(e):
        e += timedelta(days=1)
    return e


def plan(start: str, end: str, done: set) -> list[tuple[date, date, str]]:
    todo = []
    d, last = date.fromisoformat(start), date.fromisoformat(end)
    while d <= last:
        if _calendar.is_trading_day(d):
            e = next_trading_day(d)
            todo += [(d, e, leg) for leg in LEGS if (d.isoformat(), e.isoformat(), leg) not in done]
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
        CREATE TABLE IF NOT EXISTS fetched (day TEXT, expiration TEXT, leg TEXT, status TEXT,
            rows INTEGER, PRIMARY KEY (day, expiration, leg));
        """
    )
    done = {(d, e, leg) for d, e, leg in db.execute("SELECT day, expiration, leg FROM fetched")}
    todo = plan(start, end, done)
    print(f"to fetch: {len(todo)} (day, expiry, leg) requests", flush=True)
    client = client_for(cfg)

    def one(req):
        d, e, leg = req
        which, interval, t0, t1 = LEGS[leg]
        try:
            df = client._call(
                "option_history_greeks_first_order",
                symbol="SPXW",
                expiration=e,
                date=d if which == "D" else e,
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
            for (d, e, leg), status, rows in pool.map(one, todo):
                key = (d.isoformat(), e.isoformat(), leg)
                if status in ("unauthenticated", "session_in_use"):
                    raise SystemExit(f"stopped at {key}: {status}")
                db.executemany(
                    "INSERT OR REPLACE INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [(*key, *row) for row in rows],
                )
                db.execute("INSERT OR REPLACE INTO fetched VALUES (?,?,?,?,?)", (*key, status, len(rows)))
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
