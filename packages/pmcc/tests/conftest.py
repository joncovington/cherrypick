"""Session-wide test setup: a managed home that is never real, plus a stream-cache builder.

The cache fixture builds against the REAL `cherrypick.core.streamcache` DDL (the flies/calendars
pattern), so an upstream schema change fails here rather than silently producing empty snapshots.
"""

import sqlite3
import time

import pytest
from cherrypick.core import streamcache as _sc


@pytest.fixture(autouse=True)
def managed_home(tmp_path, monkeypatch):
    """Point `CHERRYPICK_HOME` at a temporary directory for every test in the suite.

    Autouse, and deliberately not something a test opts into — flies learned this on 2026-07-20,
    when three tests that skipped the opt-in fixture wrote into the real trading home mid-session
    and the day never settled. `PMCC_DB_PATH` and `PMCC_CONFIG` are cleared for the same reason: an
    operator's shell may carry them, and they resolve to real files.
    """
    home = tmp_path / "cherrypick-home"
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    monkeypatch.delenv("PMCC_DB_PATH", raising=False)
    monkeypatch.delenv("PMCC_CONFIG", raising=False)
    return home


def occ(root: str, expiration: str, strike: float, right: str = "C") -> str:
    """A real OCC symbol: padded root + yymmdd + right + strike*1000 over 8 digits."""
    yymmdd = expiration[2:4] + expiration[5:7] + expiration[8:10]
    return f"{root:<6}{yymmdd}{right}{int(round(strike * 1000)):08d}"


def streamer_sym(root: str, expiration: str, strike: float, right: str = "C") -> str:
    yymmdd = expiration[2:4] + expiration[5:7] + expiration[8:10]
    return f".{root}{yymmdd}{right}{strike:g}"


class CacheBuilder:
    """A stream cache written through the real core DDL."""

    def __init__(self, path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_sc.DDL)
        self.conn.commit()

    def spot(self, symbol: str, last: float, *, age: float = 0.0):
        self.conn.execute(
            "INSERT OR REPLACE INTO stream_trades (symbol, last, updated_at) VALUES (?, ?, ?)",
            (symbol, last, time.time() - age),
        )
        self.conn.commit()
        return self

    def option(
        self,
        symbol: str,
        expiration: str,
        strike: float,
        *,
        right: str = "C",
        root: str | None = None,
        bid: float | None = None,
        ask: float | None = None,
        delta: float | None = None,
        iv: float | None = None,
        age: float = 0.0,
    ):
        """One chain row, optionally quoted and greeked."""
        import json

        root = root or symbol
        sym = streamer_sym(root, expiration, strike, right)
        payload = {
            "streamer_symbol": sym,
            "strike_price": strike,
            "symbol": occ(root, expiration, strike, right),
            "option_type": "C" if right == "C" else "P",
        }
        self.conn.execute(
            "INSERT OR REPLACE INTO stream_chain (streamer_symbol, expiration, underlying_symbol, "
            "data_json, updated_at) VALUES (?, ?, ?, ?, ?)",
            (sym, expiration, symbol, json.dumps(payload), time.time()),
        )
        if bid is not None and ask is not None:
            self.conn.execute(
                "INSERT OR REPLACE INTO stream_quotes (symbol, bid, ask, mid, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (sym, bid, ask, (bid + ask) / 2.0, time.time() - age),
            )
        if delta is not None or iv is not None:
            self.conn.execute(
                "INSERT OR REPLACE INTO stream_greeks (symbol, delta, iv, updated_at) VALUES (?, ?, ?, ?)",
                (sym, delta, iv, time.time() - age),
            )
        self.conn.commit()
        return sym

    def summary(self, symbol: str, trade_date: str, *, o=None, h=None, low=None, c=None, prev=None):
        self.conn.execute(
            "INSERT OR REPLACE INTO stream_summary (symbol, trade_date, day_open, day_high, "
            "day_low, day_close, prev_day_close, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, trade_date, o, h, low, c, prev, time.time()),
        )
        self.conn.commit()
        return self


@pytest.fixture
def cache(tmp_path):
    return CacheBuilder(tmp_path / "stream_cache.db")


@pytest.fixture
def config():
    return {
        "symbols": ["TQQQ"],
        "occ_roots": {"TQQQ": "TQQQ"},
        "settlement_style": {"TQQQ": "physical"},
        "dividends": {"TQQQ": {"declared_through": "2099-12-31", "ex_dates": []}},
        "defaults": {
            "quantity": 1,
            "max_positions": 3,
            "long_delta_min": 0.85,
            "long_delta_max": 0.90,
            "tv_close_threshold": 0.10,
            "tv_managed_exit": False,
            "assignment_exposure_tv": 0.05,
            "max_quote_age_seconds": 300,
            "max_leg_spread_pct": 0.25,
            "deep_window_pct": 0.20,
        },
        "books": {
            "control": {"enabled": True},
        },
        "tastytrade_costs": {},
        "advice": {"enabled": False, "base_book": "control", "bounds": {}},
    }
