"""A store in the gex recorder's schema, written for tests: the tables `core.quotehistory` and
`core.regime` read (samples, quotes, spot, GEX profile, daily closes, market regime). The columns are
the ones those readers query; the real DDL lives in gex and backtest, which this package may not
import."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from cherrypick.core.clock import ET

DDL = """
CREATE TABLE gex_quote_samples (id INTEGER PRIMARY KEY, symbol TEXT, trade_date TEXT, ts REAL,
    expiration TEXT);
CREATE TABLE gex_quotes (sample_id INTEGER, side INTEGER, strike REAL, bid REAL, ask REAL, mid REAL,
    age_s REAL, delta REAL, delta_age_s REAL);
CREATE TABLE gex_spot_history (symbol TEXT, trade_date TEXT, ts REAL, spot REAL);
CREATE TABLE gex_profile_history (symbol TEXT, trade_date TEXT, ts REAL, strike REAL, call_oi REAL,
    put_oi REAL, call_vol REAL, put_vol REAL, call_gamma REAL, put_gamma REAL, call_iv REAL, put_iv REAL);
CREATE TABLE daily_closes (symbol TEXT, trade_date TEXT, close REAL, recorded_at REAL, source TEXT);
CREATE TABLE market_regime_history (trade_date TEXT, ts REAL, reading TEXT, symbol TEXT, value REAL,
    basis_ts REAL, usable INTEGER, reason TEXT);
"""


def ts_of(day: str, minute: int) -> float:
    return datetime.fromisoformat(day).replace(hour=minute // 60, minute=minute % 60, tzinfo=ET).timestamp()


class StoreBuilder:
    def __init__(self, path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.executescript(DDL)

    def close(self, symbol: str, day: str, price: float):
        self.conn.execute("INSERT INTO daily_closes VALUES (?, ?, ?, 0, 'test')", (symbol, day, price))
        self.conn.commit()
        return self

    def minute(
        self, symbol: str, day: str, minute: int, expiry: str, spot: float, rows: list[dict], *, vix=None
    ):
        """One recorded instant: the chain sample, the spot, the profile from the rows' gamma and OI."""
        ts = ts_of(day, minute)
        cur = self.conn.execute(
            "INSERT INTO gex_quote_samples (symbol, trade_date, ts, expiration) VALUES (?, ?, ?, ?)",
            (symbol, day, ts, expiry),
        )
        sid = cur.lastrowid
        prof: dict[float, dict] = {}
        for r in rows:
            side = 1 if r["right"] == "C" else 0
            self.conn.execute(
                "INSERT INTO gex_quotes VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL)",
                (sid, side, r["strike"], r["bid"], r["ask"], (r["bid"] + r["ask"]) / 2, r.get("delta")),
            )
            p = prof.setdefault(r["strike"], {})
            key = "call" if r["right"] == "C" else "put"
            p[f"{key}_gamma"], p[f"{key}_oi"] = r.get("gamma"), r.get("oi")
        for k, p in prof.items():
            self.conn.execute(
                "INSERT INTO gex_profile_history VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?, NULL, NULL)",
                (
                    symbol,
                    day,
                    ts,
                    k,
                    p.get("call_oi"),
                    p.get("put_oi"),
                    p.get("call_gamma"),
                    p.get("put_gamma"),
                ),
            )
        self.conn.execute("INSERT INTO gex_spot_history VALUES (?, ?, ?, ?)", (symbol, day, ts, spot))
        if vix is not None:
            self.conn.execute(
                "INSERT INTO market_regime_history VALUES (?, ?, 'vix', 'VIX', ?, ?, 1, NULL)",
                (day, ts, vix, ts),
            )
        self.conn.commit()
        return self
