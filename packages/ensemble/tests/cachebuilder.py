"""A stream cache built through the real core DDL and holding Black-priced chains, daily bars and prints, so
a loop test reads exactly what the streamer would have written. Invented numbers only."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import date, timedelta

from cherrypick.core import calendar as cal
from cherrypick.core import streamcache as sc
from market import rows as market_rows


class CacheBuilder:
    def __init__(self, path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(sc.DDL)
        self.conn.commit()

    def last(self, symbol: str, value: float, *, age: float = 0.0):
        self.conn.execute(
            "INSERT OR REPLACE INTO stream_trades (symbol, last, updated_at) VALUES (?, ?, ?)",
            (symbol, value, time.time() - age),
        )
        self.conn.commit()
        return self

    def chain(
        self, symbol: str, expiration: str, root: str, spot: float, years: float, *, vol=0.2, age=0.0, **kw
    ):
        """Both rights at every strike around `spot`, quoted, with delta/gamma/iv and open interest."""
        compact = expiration[2:].replace("-", "")
        for r in market_rows(spot, years, vol, **kw):
            k, right = r["strike"], r["right"]
            streamer = f".{root}{compact}{right}{k:g}"
            occ = f"{root:<6}{compact}{right}{int(round(k * 1000)):08d}"
            data = {
                "streamer_symbol": streamer,
                "strike_price": k,
                "symbol": occ,
                "option_type": "C" if right == "C" else "P",
            }
            now = time.time()
            self.conn.execute(
                "INSERT OR REPLACE INTO stream_chain (streamer_symbol, expiration, underlying_symbol, "
                "data_json, updated_at) VALUES (?, ?, ?, ?, ?)",
                (streamer, expiration, symbol, json.dumps(data), now),
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO stream_quotes (symbol, bid, ask, mid, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (streamer, r["bid"], r["ask"], (r["bid"] + r["ask"]) / 2.0, now - age),
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO stream_greeks (symbol, delta, gamma, iv, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (streamer, r["delta"], r["gamma"], r["iv"], now - age),
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO stream_oi (symbol, open_interest, updated_at) VALUES (?, ?, ?)",
                (streamer, r["oi"], now),
            )
        self.conn.commit()
        return self

    def listed(self, symbol: str, expirations):
        sc.write_listed_expirations(self.conn, symbol, expirations)
        return self

    def summary(self, symbol: str, trade_date: str, *, close=None, prev_close=None, day_open=None):
        self.conn.execute(
            "INSERT OR REPLACE INTO stream_summary (symbol, trade_date, day_open, day_high, day_low, "
            "day_close, prev_day_close, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, trade_date, day_open, close, close, close, prev_close, time.time()),
        )
        self.conn.commit()
        return self

    def history(self, symbol: str, before: str, closes: list[float]):
        """`closes` as the daily bars of the sessions ending the session before `before`, oldest first."""
        d = date.fromisoformat(before)
        days = []
        for _ in closes:
            d = cal.previous_trading_day(d)
            days.append(d)
        for day, close in zip(reversed(days), closes, strict=True):
            self.summary(symbol, day.isoformat(), close=close)
        return self


def sessions_before(day: str, n: int) -> list[str]:
    d, out = date.fromisoformat(day), []
    for _ in range(n):
        d = cal.previous_trading_day(d)
        out.append(d.isoformat())
    return list(reversed(out))


def next_day(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()
