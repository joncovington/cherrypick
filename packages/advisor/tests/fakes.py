"""Fabricated module databases, built from each module's REAL DDL.

Copied deliberately, not imported: importing `cherrypick.meic.db` to build a fixture would give this
package a dependency on every module it reads, which is precisely the coupling the read-only posture
exists to avoid (and the guardrail scan would fail on it). The cost is that these definitions can
drift from the modules'; the mitigation is that they carry only the columns the fact pack actually
selects, so drift shows up as a failing pack test rather than as silently wrong facts.

Column lists here are trimmed to what `factpack.py` reads. Where a module's real table has forty
columns, the fixture has the eight that matter.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

MEIC_DDL = """
CREATE TABLE ic_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, trade_date TEXT NOT NULL, entry_time TEXT,
    symbol TEXT NOT NULL, wing_width REAL, net_credit REAL, quantity INTEGER DEFAULT 1,
    risk_profile TEXT, pnl REAL, fees REAL, status TEXT, exit_time TEXT,
    put_max_cost REAL, call_max_cost REAL, slippage_dollars REAL DEFAULT 0,
    ic_order_id TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE market_context (
    context_date TEXT PRIMARY KEY, vix REAL, vix1d REAL, vix1d_ratio REAL,
    symbols_json TEXT DEFAULT '{}', updated_at TEXT NOT NULL
);
CREATE TABLE iteration_regime (
    id INTEGER PRIMARY KEY AUTOINCREMENT, loop_date TEXT NOT NULL, loop_time TEXT NOT NULL,
    symbol TEXT NOT NULL, underlying_price REAL, entries_n INTEGER DEFAULT 0,
    blocked_n INTEGER DEFAULT 0, vol_implied_bucket TEXT, vol_event_bucket TEXT,
    vol_realized_bucket TEXT, gex_bucket TEXT, trend_bucket TEXT, created_at TEXT NOT NULL
);
CREATE TABLE entry_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, trade_date TEXT NOT NULL,
    risk_profile TEXT NOT NULL, symbol TEXT NOT NULL, outcome TEXT NOT NULL,
    block_detail TEXT, underlying_price REAL, would_be_credit REAL, ic_order_id TEXT
);
CREATE TABLE loop_log (id INTEGER PRIMARY KEY AUTOINCREMENT, loop_date TEXT, reasoning TEXT);
"""

FLIES_DDL = """
CREATE TABLE fly_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, position_id TEXT UNIQUE, book_id TEXT, trade_date TEXT,
    arm TEXT, symbol TEXT, kind TEXT, quantity INTEGER, net REAL, fees REAL,
    gross_pnl REAL, pnl REAL, status TEXT, entry_time TEXT, exit_time TEXT,
    completion_latency_min REAL, entry_fill_status TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE fly_books (
    id INTEGER PRIMARY KEY AUTOINCREMENT, book_id TEXT UNIQUE, trade_date TEXT, arm TEXT,
    symbol TEXT, credit_collected REAL, debits_paid REAL, fees REAL, net_cash REAL, worst REAL,
    floor_holds INTEGER, band_low REAL, band_high REAL, unbounded_below INTEGER,
    completion_rate REAL, modeled_pnl REAL, pnl REAL, status TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE fly_entry_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, trade_date TEXT, arm TEXT, symbol TEXT,
    mode TEXT, outcome TEXT, block_detail TEXT, center REAL, spot REAL, would_be_credit REAL,
    position_id TEXT
);
CREATE TABLE fly_iterations (id INTEGER PRIMARY KEY AUTOINCREMENT, trade_date TEXT, ts TEXT);
"""

EARNINGS_DDL = """
CREATE TABLE trades (
    order_id TEXT PRIMARY KEY, strategy TEXT NOT NULL DEFAULT 'iron_fly', symbol TEXT NOT NULL,
    expiration TEXT NOT NULL, legs_json TEXT, entry_credit REAL, exit_debit REAL, pnl REAL,
    opened_at REAL, closed_at REAL, profile TEXT NOT NULL DEFAULT 'default', quantity INTEGER,
    capital_at_risk REAL, entry_cost REAL, exit_cost REAL, entry_context TEXT,
    entry_slippage REAL, exit_slippage REAL,
    status TEXT NOT NULL DEFAULT 'open', exit_reason TEXT, hold_days INTEGER,
    max_unrealized_pnl REAL, min_unrealized_pnl REAL
);
CREATE TABLE scan_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scan_date TEXT NOT NULL,
    strategy TEXT NOT NULL DEFAULT 'iron_fly', symbol TEXT NOT NULL, tier TEXT, outcome TEXT,
    reason TEXT, stage TEXT NOT NULL DEFAULT 'screen', reject_details TEXT, logged_at REAL,
    profile TEXT NOT NULL DEFAULT 'default'
);
CREATE TABLE position_marks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, marked_at REAL NOT NULL,
    session_date TEXT NOT NULL, exit_debit REAL, unrealized_pnl REAL, spot REAL, source TEXT,
    usable INTEGER NOT NULL DEFAULT 0, refusal TEXT
);
CREATE TABLE management_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, occurred_at REAL NOT NULL,
    session_date TEXT NOT NULL, phase TEXT, action TEXT NOT NULL, reason TEXT NOT NULL,
    executed INTEGER NOT NULL DEFAULT 0, gate TEXT, detail_json TEXT, mark_id INTEGER
);
CREATE TABLE loop_iterations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ran_at REAL NOT NULL, session_date TEXT NOT NULL,
    phase TEXT NOT NULL, status TEXT NOT NULL, open_positions INTEGER, marks_written INTEGER,
    actions_taken INTEGER, open_capital REAL, duration_ms INTEGER, note TEXT
);
"""

STREAM_CACHE_DDL = """
CREATE TABLE stream_summary (
    symbol TEXT NOT NULL, trade_date TEXT NOT NULL, day_open REAL, day_high REAL, day_low REAL,
    day_close REAL, prev_day_close REAL, updated_at REAL NOT NULL,
    PRIMARY KEY (symbol, trade_date)
);
"""

GEX_DDL = """
CREATE TABLE gex_regime_history (
    symbol TEXT NOT NULL, trade_date TEXT NOT NULL, ts REAL NOT NULL, spot REAL, net_gex REAL,
    net_gex_vol REAL, zero_gamma REAL, call_wall REAL, put_wall REAL, expiration TEXT
);
"""


def make_db(path: Path, ddl: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(ddl)
    conn.commit()
    conn.close()
    return path


def insert(path: Path, table: str, rows: list[dict]) -> None:
    conn = sqlite3.connect(path)
    for row in rows:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))
    conn.commit()
    conn.close()


def seed_suite(home: Path, session: str, *, with_live: bool = True) -> None:
    """A home that looks like a machine which has been trading: three modules, a stream cache, a
    GEX history, and (optionally) live ledgers beside the paper ones."""
    data = home / "data"

    meic = make_db(data / "meic" / "paper_trades.db", MEIC_DDL)
    insert(meic, "market_context", [
        {"context_date": session, "vix": 15.4, "vix1d": 12.2, "vix1d_ratio": 0.79,
         "updated_at": f"{session}T13:00:00+00:00"},
    ])
    insert(meic, "entry_attempts", [
        {"ts": f"{session}T14:31:00", "trade_date": session, "risk_profile": "control",
         "symbol": "SPX", "outcome": "filled", "ic_order_id": "ic-1"},
        {"ts": f"{session}T14:41:00", "trade_date": session, "risk_profile": "control",
         "symbol": "SPX", "outcome": "gate_blocked", "block_detail": "regime_gex_negative"},
        {"ts": f"{session}T14:51:00", "trade_date": session, "risk_profile": "width-5",
         "symbol": "SPX", "outcome": "cadence_blocked", "block_detail": "cadence_not_clear"},
    ])
    insert(meic, "ic_trades", [
        {"trade_date": session, "symbol": "SPX", "risk_profile": "control", "net_credit": 2.4,
         "wing_width": 20, "quantity": 1, "pnl": 180.0, "fees": 6.0, "status": "closed",
         "exit_time": f"{session}T20:10:00", "put_max_cost": 3.2,
         "ic_order_id": "ic-1", "created_at": f"{session}T14:31:00"},
        {"trade_date": session, "symbol": "SPX", "risk_profile": "advised:control",
         "net_credit": 2.4, "wing_width": 20, "quantity": 1, "pnl": 210.0, "fees": 6.0,
         "status": "closed", "exit_time": f"{session}T20:10:00",
         "ic_order_id": "ic-2", "created_at": f"{session}T14:31:00"},
    ])
    insert(meic, "iteration_regime", [
        {"loop_date": session, "loop_time": "15:05:00", "symbol": "SPX", "underlying_price": 5600.0,
         "vol_implied_bucket": "low", "gex_bucket": "positive", "trend_bucket": "flat",
         "created_at": f"{session}T15:05:00"},
    ])

    flies = make_db(data / "flies" / "paper_trades.db", FLIES_DDL)
    insert(flies, "fly_books", [
        {"book_id": "b1", "trade_date": session, "arm": "control", "symbol": "SPX",
         "credit_collected": 420.0, "debits_paid": 260.0, "fees": 18.0, "net_cash": 142.0,
         "worst": -85.0, "floor_holds": 1, "band_low": 5540.0, "band_high": 5660.0,
         "unbounded_below": 0, "completion_rate": 0.66, "modeled_pnl": 120.0, "pnl": 142.0,
         "status": "settled"},
    ])
    insert(flies, "fly_entry_attempts", [
        {"ts": f"{session}T14:00:00", "trade_date": session, "arm": "control", "symbol": "SPX",
         "mode": "legged", "outcome": "filled", "position_id": "p1"},
        {"ts": f"{session}T14:20:00", "trade_date": session, "arm": "control", "symbol": "SPX",
         "mode": "legged", "outcome": "gate_blocked", "block_detail": "credit_below_floor"},
    ])
    insert(flies, "fly_positions", [
        {"position_id": "p1", "book_id": "b1", "trade_date": session, "arm": "control",
         "symbol": "SPX", "kind": "fly", "net": 60.0, "fees": 9.0, "gross_pnl": 151.0,
         "pnl": 142.0, "status": "settled", "completion_latency_min": 12.0},
    ])

    earnings = make_db(data / "earnings" / "paper_trades.db", EARNINGS_DDL)
    opened = 1_754_000_000.0
    insert(earnings, "trades", [
        {"order_id": "e-1", "strategy": "iron_fly", "symbol": "AAPL", "expiration": session,
         "entry_credit": 3.1, "pnl": None, "opened_at": opened, "profile": "strat_test:iron_fly",
         "quantity": 1, "capital_at_risk": 690.0, "entry_cost": 2.6, "status": "open",
         "hold_days": 1, "max_unrealized_pnl": 90.0, "min_unrealized_pnl": -30.0},
    ])
    insert(earnings, "position_marks", [
        {"order_id": "e-1", "marked_at": opened + 3600, "session_date": session,
         "unrealized_pnl": 42.0, "usable": 1},
        {"order_id": "e-1", "marked_at": opened + 7200, "session_date": session,
         "unrealized_pnl": None, "usable": 0, "refusal": "quotes_stale"},
    ])
    insert(earnings, "scan_log", [
        {"scan_date": session, "strategy": "iron_fly", "symbol": "AAPL", "outcome": "accepted",
         "stage": "screen", "profile": "strat_test:iron_fly"},
        {"scan_date": session, "strategy": "iron_fly", "symbol": "TSLA", "outcome": "rejected",
         "reason": "iv_rv_ratio_below_floor", "stage": "screen"},
    ])
    insert(earnings, "loop_iterations", [
        {"ran_at": opened + 100, "session_date": session, "phase": "manage", "status": "ok",
         "open_positions": 1},
    ])
    insert(earnings, "management_events", [
        {"order_id": "e-1", "occurred_at": opened + 200, "session_date": session, "phase": "manage",
         "action": "hold", "reason": "target_not_hit", "executed": 0},
    ])

    cache = make_db(data / "marketdata" / "stream_cache.db", STREAM_CACHE_DDL)
    insert(cache, "stream_summary", [
        {"symbol": "SPX", "trade_date": session, "day_open": 5590.0, "day_high": 5620.0,
         "day_low": 5570.0, "day_close": 5605.0, "prev_day_close": 5580.0, "updated_at": opened},
        # Yesterday's row: the pack must not report it as today's range.
        {"symbol": "SPX", "trade_date": "1999-01-04", "day_open": 1.0, "day_high": 2.0,
         "day_low": 0.5, "day_close": 1.5, "prev_day_close": 1.0, "updated_at": 0.0},
    ])

    gex = make_db(data / "gex" / "gex_history.db", GEX_DDL)
    insert(gex, "gex_regime_history", [
        {"symbol": "SPX", "trade_date": session, "ts": opened, "spot": 5600.0, "net_gex": 1.2e9,
         "zero_gamma": 5570.0, "call_wall": 5650.0, "put_wall": 5500.0},
        {"symbol": "SPX", "trade_date": session, "ts": opened + 300, "spot": 5605.0,
         "net_gex": -0.4e9, "zero_gamma": 5580.0},
    ])

    if with_live:
        live = make_db(data / "flies" / "live_trades.db", FLIES_DDL)
        insert(live, "fly_positions", [
            {"position_id": "lp1", "book_id": "lb1", "trade_date": session, "arm": "control",
             "symbol": "SPX", "kind": "fly", "net": 40.0, "fees": 3.0, "pnl": 37.0,
             "status": "settled", "entry_fill_status": "filled"},
        ])
        make_db(data / "meic" / "meic_trades.db", MEIC_DDL)
        make_db(data / "earnings" / "earnings_trades.db", EARNINGS_DDL)


def write_config(home: Path, module: str, config: dict) -> Path:
    import json

    path = home / "config" / f"{module}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


def advice_block(bounds: dict, *, enabled: bool = True, base_key: str = "base_profile",
                 base: str = "control") -> dict:
    return {"advice": {"enabled": enabled, base_key: base, "bounds": bounds}}


def write_suite_config(home: Path, advisor: dict) -> Path:
    """The orchestrator's `~/.cherrypick/config.json`, carrying an `advisor` block."""
    import json

    path = home / "config.json"
    path.write_text(json.dumps({"advisor": advisor}, indent=2), encoding="utf-8")
    return path
