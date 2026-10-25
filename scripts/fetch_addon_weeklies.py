"""Fetch SPXW weekly (PM Friday) put minute greeks for each expiry's whole bwb life, plus the ATM
calls at 10:00 for the expected move. Study store: <vendor_dir>/addon_weeklies.db (licensed dir).
Resumable: an expiry marked complete is skipped.

    python scripts/fetch_addon_weeklies.py [START END]   # expiries, default 2022-05-20 2026-10-16

The store the bwb add-on standalone study read (2026-10-23; packages/backtest `stores.STORES`).
Committed 2026-10-25 from the session scratch it was written in, its behaviour unchanged, so
the store can be rebuilt. It holds the vendor session while it runs; queue it, or run it outside the
backtest fetch window.
"""

import math
import sqlite3
import sys
import time
from datetime import date, timedelta

from cherrypick.backtest import paths
from cherrypick.backtest import settings as _settings
from cherrypick.backtest.vendor import FATAL, VendorRefusal, client_for

START = sys.argv[1] if len(sys.argv) > 1 else "2022-05-20"
END = sys.argv[2] if len(sys.argv) > 2 else "2026-10-16"
STRIKE_RANGE = 60

s = _settings.load()
db = paths.vendor_dir(s) / "addon_weeklies.db"
conn = sqlite3.connect(db)
conn.executescript(
    """
CREATE TABLE IF NOT EXISTS q (expiration TEXT, day TEXT, ts INTEGER, right TEXT, strike REAL,
  bid REAL, ask REAL, delta REAL, iv REAL, spot REAL);
CREATE INDEX IF NOT EXISTS q_ix ON q (expiration, ts);
CREATE TABLE IF NOT EXISTS fetched (expiration TEXT PRIMARY KEY, status TEXT, rows INTEGER,
  detail TEXT, at REAL);
"""
)


def num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


client = client_for(s)
exps = [e for e in client.expirations("SPXW") if START <= e <= END and date.fromisoformat(e).weekday() == 4]
done = {r[0] for r in conn.execute("SELECT expiration FROM fetched WHERE status='complete'")}
print(f"{len(exps)} Friday expiries, {len(done)} done", flush=True)
prev = None
for e in exps:
    f = date.fromisoformat(e)
    first = f - timedelta(days=7)  # the prior Friday enters this expiry
    if e in done:
        continue
    t0 = time.time()
    try:
        rows = []
        for right, st, et, rng in (
            ("put", "09:30:00", "16:15:00", STRIKE_RANGE),
            ("call", "10:00:00", "10:01:00", 3),
        ):
            df = client._call(
                "option_history_greeks_first_order",
                symbol="SPXW",
                expiration=f,
                interval="1m",
                right=right,
                start_date=first,
                end_date=f,
                start_time=st,
                end_time=et,
                strike_range=rng,
            )
            for r in df.itertuples(index=False):
                spot = num(r.underlying_price)
                k = float(r.strike)
                if right == "put" and spot and k > spot + 50:
                    continue
                ts = r.timestamp
                rows.append(
                    (
                        e,
                        ts.date().isoformat(),
                        int(ts.timestamp()),
                        right[0].upper(),
                        k,
                        num(r.bid),
                        num(r.ask),
                        num(r.delta),
                        num(r.implied_vol),
                        spot,
                    )
                )
        conn.execute("DELETE FROM q WHERE expiration=?", (e,))
        conn.executemany("INSERT INTO q VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        conn.execute(
            "INSERT OR REPLACE INTO fetched VALUES (?,?,?,?,?)", (e, "complete", len(rows), None, time.time())
        )
        conn.commit()
        print(f"{e} {len(rows)} rows {time.time() - t0:.0f}s", flush=True)
    except VendorRefusal as ref:
        conn.execute(
            "INSERT OR REPLACE INTO fetched VALUES (?,?,?,?,?)",
            (e, "refused", 0, f"{ref.reason}: {ref.detail[:300]}", time.time()),
        )
        conn.commit()
        print(f"{e} refused {ref.reason}", flush=True)
        if ref.reason in FATAL:
            break
client.close()
