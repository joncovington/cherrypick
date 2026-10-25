"""Fetch SPY standard-monthly chains at 15:45 ET for every trading day, into the licensed store.

    python scripts/fetch_spy_monthly_1545.py 2017-01-03 2026-10-16

Builds `SPY-monthly-1545.db` in the backtest vendor directory (packages/backtest `stores.STORES`): the
store the strangle-management test read (2026-10-20). Committed 2026-10-25 from the session scratch
it was written in, its behaviour unchanged, so the store can be rebuilt.

Resumable: a (day, expiration) already recorded (with rows, or as refused) is skipped. Writes only
under the backtest vendor directory (marked licensed). Prints progress counts only.
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

MAX_DTE = 70
STRIKE_RANGE = 90
WORKERS = 4


def third_friday(y, m):
    d = date(y, m, 15)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def monthly(y, m):
    tf = third_friday(y, m)
    return tf if _calendar.is_trading_day(tf) else tf - timedelta(days=1)


def monthlies_live(d):
    out, y, m = [], d.year, d.month
    for _ in range(4):
        e = monthly(y, m)
        if 0 < (e - d).days <= MAX_DTE:
            out.append(e)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def main(start, end):
    cfg = _settings.load()
    db_path = paths.ensure_vendor_dir(cfg) / "SPY-monthly-1545.db"
    db = sqlite3.connect(db_path)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS quotes (day TEXT, expiration TEXT, strike REAL, right TEXT,
            bid REAL, ask REAL, delta REAL, iv REAL, underlying REAL,
            PRIMARY KEY (day, expiration, strike, right));
        CREATE TABLE IF NOT EXISTS fetched (day TEXT, expiration TEXT, status TEXT, rows INTEGER,
            PRIMARY KEY (day, expiration));
        """
    )
    done = {(d, e) for d, e in db.execute("SELECT day, expiration FROM fetched")}
    todo = []
    d, last = date.fromisoformat(start), date.fromisoformat(end)
    while d <= last:
        if _calendar.is_trading_day(d):
            todo += [(d, e) for e in monthlies_live(d) if (d.isoformat(), e.isoformat()) not in done]
        d += timedelta(days=1)
    print(f"to fetch: {len(todo)} (day, expiry) pairs", flush=True)
    client = client_for(cfg)

    def one(pair):
        day, exp = pair
        try:
            df = client._call(
                "option_history_greeks_first_order",
                symbol="SPY",
                expiration=exp,
                date=day,
                interval="1m",
                strike_range=STRIKE_RANGE,
                start_time="15:45:00",
                end_time="15:46:00",
            )
        except VendorRefusal as refusal:
            return pair, refusal.reason, {}
        rows = {}
        for r in df.itertuples(index=False):
            rows[(float(r.strike), str(r.right).upper()[0])] = r  # the later bar wins
        return pair, "ok", rows

    n_req, n_rows, t0 = 0, 0, time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for (day, exp), status, rows in pool.map(one, todo):
                key = (day.isoformat(), exp.isoformat())
                if status in ("unauthenticated", "session_in_use"):
                    raise SystemExit(f"stopped at {key}: {status}")
                db.executemany(
                    "INSERT OR REPLACE INTO quotes VALUES (?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            key[0],
                            key[1],
                            k[0],
                            k[1],
                            _f(r.bid),
                            _f(r.ask),
                            _f(r.delta),
                            _f(r.implied_vol),
                            _f(r.underlying_price),
                        )
                        for k, r in rows.items()
                    ],
                )
                db.execute("INSERT OR REPLACE INTO fetched VALUES (?,?,?,?)", (*key, status, len(rows)))
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
