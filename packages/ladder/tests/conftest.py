"""Session-wide test setup: a managed home that is never real, and a stream cache built through the
real core DDL holding a fund's quote and its daily summary."""

import sqlite3
import time
from datetime import datetime

import pytest
from cherrypick.core import streamcache as _sc
from cherrypick.core.clock import ET


@pytest.fixture(autouse=True)
def managed_home(tmp_path, monkeypatch):
    """Autouse, never opt-in (flies, 2026-07-20: tests that skipped the opt-in wrote into the real
    home mid-session)."""
    home = tmp_path / "cherrypick-home"
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    monkeypatch.delenv("LADDER_DB_PATH", raising=False)
    monkeypatch.delenv("LADDER_CONFIG", raising=False)
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

    def summary(self, symbol: str, trade_date: str, *, day_close=None, prev_day_close=None):
        self.conn.execute(
            "INSERT OR REPLACE INTO stream_summary (symbol, trade_date, day_open, day_high, day_low, "
            "day_close, prev_day_close, updated_at) VALUES (?, ?, NULL, NULL, NULL, ?, ?, ?)",
            (symbol, trade_date, day_close, prev_day_close, time.time()),
        )
        self.conn.commit()
        return self


@pytest.fixture
def cache(tmp_path):
    return CacheBuilder(tmp_path / "stream_cache.db")


@pytest.fixture
def config():
    return {
        "defaults": {
            "lot_dollars": 1000,
            "min_shares": 3,
            "max_open_lots": 3,
            "max_open_lots_total": 6,
            "drop": 0.07,
            "gain": 0.04,
            "max_quote_age_seconds": 120,
            "session_start": "08:15",
            "session_end": "20:00",
        },
        "names": {"TQQQ": {}},
        "arms": {
            "chain": {"entry": "chain", "target": "fixed"},
            "once": {"entry": "once", "target": "fixed"},
            "decay": {"entry": "once", "target": "decay"},
        },
    }


@pytest.fixture
def ledger(tmp_path):
    from cherrypick.ladder import db

    return db.connect(str(tmp_path / "paper_trades.db"))


def at(day: str, hhmm: str) -> datetime:
    h, m = hhmm.split(":")
    return datetime.fromisoformat(day).replace(hour=int(h), minute=int(m), tzinfo=ET)
