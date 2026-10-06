"""Session-wide test setup: a managed home that is never real, a stream cache built through the real
core DDL, and a technicals store holding only what this module reads (the dividend table)."""

import sqlite3
import time

import pytest
from cherrypick.core import streamcache as _sc


@pytest.fixture(autouse=True)
def managed_home(tmp_path, monkeypatch):
    """Autouse, never opt-in (flies, 2026-07-20: tests that skipped the opt-in wrote into the real
    home mid-session)."""
    home = tmp_path / "cherrypick-home"
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    monkeypatch.delenv("CONTANGO_DB_PATH", raising=False)
    monkeypatch.delenv("CONTANGO_CONFIG", raising=False)
    return home


class CacheBuilder:
    def __init__(self, path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(_sc.DDL)
        self.conn.commit()

    def quote(self, symbol: str, bid: float, ask: float, *, age: float = 0.0):
        self.conn.execute(
            "INSERT OR REPLACE INTO stream_quotes (symbol, bid, ask, mid, updated_at) VALUES (?, ?, ?, ?, ?)",
            (symbol, bid, ask, (bid + ask) / 2.0, time.time() - age),
        )
        self.conn.commit()
        return self

    def last(self, symbol: str, value: float, *, age: float = 0.0):
        self.conn.execute(
            "INSERT OR REPLACE INTO stream_trades (symbol, last, updated_at) VALUES (?, ?, ?)",
            (symbol, value, time.time() - age),
        )
        self.conn.commit()
        return self

    def regime(self, ratio: float, *, age: float = 0.0):
        return self.last("VIX", 20.0 * ratio, age=age).last("VIX3M", 20.0, age=age)


@pytest.fixture
def cache(tmp_path):
    return CacheBuilder(tmp_path / "stream_cache.db")


@pytest.fixture
def technicals(tmp_path):
    path = tmp_path / "history.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE dividends (symbol TEXT NOT NULL, ex_date TEXT NOT NULL, amount REAL NOT NULL, "
        "PRIMARY KEY (symbol, ex_date))"
    )
    conn.commit()
    conn.close()
    return path


def add_dividend(path, symbol: str, ex_date: str, amount: float) -> None:
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO dividends VALUES (?, ?, ?)", (symbol, ex_date, amount))
    conn.commit()
    conn.close()


@pytest.fixture
def config():
    return {
        "defaults": {
            "risk_symbol": "SVXY",
            "cash_symbol": "SHV",
            "starting_capital": 10000,
            "enter_below": 0.97,
            "exit_at_or_above": 0.97,
            "decision_minutes_before_close": 10,
            "decision_window_minutes": 8,
            "max_quote_age_seconds": 120,
            "max_spread_bps": 50,
            "slippage_floor_bps": 2,
        },
        "arms": {"control": {"enabled": True}, "flipexit": {"enabled": True, "exit_at_or_above": 1.0}},
    }
