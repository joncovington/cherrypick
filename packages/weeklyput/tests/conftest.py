"""Session-wide test setup: a managed home that is never real, and a stream cache built through the
real core DDL holding Black-priced chains, so a test knows which strike is the 10-delta put."""

import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest
from cherrypick.core import bs
from cherrypick.core import streamcache as _sc

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def managed_home(tmp_path, monkeypatch):
    """Autouse, never opt-in (flies, 2026-07-20: tests that skipped the opt-in wrote into the real
    home mid-session)."""
    home = tmp_path / "cherrypick-home"
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    monkeypatch.delenv("WEEKLYPUT_DB_PATH", raising=False)
    monkeypatch.delenv("WEEKLYPUT_CONFIG", raising=False)
    return home


def black_chain(fwd: float, vol: float, years: float, strikes, *, half_spread: float = 0.05):
    """(strike, right, bid, ask) for calls and puts priced by Black on `fwd` at zero rate."""
    out = []
    for k in strikes:
        for right in ("C", "P"):
            m = bs.price(fwd, k, years, vol, right, 0.0)
            if m < 0.06:
                continue
            out.append((float(k), right, round(m - half_spread, 2), round(m + half_spread, 2)))
    return out


class CacheBuilder:
    def __init__(self, path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(_sc.DDL)
        self.conn.commit()

    def last(self, symbol: str, value: float, *, age: float = 0.0):
        self.conn.execute(
            "INSERT OR REPLACE INTO stream_trades (symbol, last, updated_at) VALUES (?, ?, ?)",
            (symbol, value, time.time() - age),
        )
        self.conn.commit()
        return self

    def chain(self, symbol: str, expiration: str, root: str, rows, *, age: float = 0.0, quoted: bool = True):
        """List each (strike, right) in the chain metadata; quote it unless `quoted` is False."""
        compact = expiration[2:].replace("-", "")
        for k, right, bid, ask in rows:
            ks = f"{k:g}"
            streamer = f".{root}{compact}{right}{ks}"
            occ = f"{root:<6}{compact}{right}{int(round(k * 1000)):08d}"
            data = {
                "streamer_symbol": streamer,
                "strike_price": k,
                "symbol": occ,
                "option_type": "C" if right == "C" else "P",
            }
            self.conn.execute(
                "INSERT OR REPLACE INTO stream_chain (streamer_symbol, expiration, underlying_symbol, "
                "data_json, updated_at) VALUES (?, ?, ?, ?, ?)",
                (streamer, expiration, symbol, json.dumps(data), time.time()),
            )
            if quoted:
                self.conn.execute(
                    "INSERT OR REPLACE INTO stream_quotes (symbol, bid, ask, mid, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (streamer, bid, ask, (bid + ask) / 2.0, time.time() - age),
                )
        self.conn.commit()
        return self

    def quote(self, streamer: str, bid: float, ask: float, *, age: float = 0.0):
        self.conn.execute(
            "INSERT OR REPLACE INTO stream_quotes (symbol, bid, ask, mid, updated_at) VALUES (?, ?, ?, ?, ?)",
            (streamer, bid, ask, (bid + ask) / 2.0, time.time() - age),
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
            "quantity": 1,
            "decision_minutes_before_close": 15,
            "decision_window_minutes": 10,
            "max_quote_age_seconds": 120,
            "put_band": [0.015, 0.12],
            "mark_interval_minutes": 30,
            "assignment_extrinsic": 0.05,
            "settle_alert_sessions": 2,
        },
        "names": {
            "SPX": {"root": "SPXW", "fee_symbol": "SPX", "settlement": "cash"},
            "SPY": {"root": "SPY", "fee_symbol": "SPY", "settlement": "physical"},
        },
        "arms": {"unhedged": {"enabled": True}},
    }


def load_script(name: str):
    """A study script, loaded by path (they are not a package): the backtest engine's studies sit in
    `packages/backtest/scripts/`, the rest in `scripts/`."""
    path = REPO / "packages" / "backtest" / "scripts" / f"{name}.py"
    if not path.is_file():
        path = REPO / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod
