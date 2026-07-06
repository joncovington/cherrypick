"""SQLite persistence for EarningsFlyAgent.

Not yet implemented. Intended commands (see CLAUDE.md's Database section):
  init_db
  get_open_positions
  save_trade --data '{...}'
  save_close --data '{...}'
  log_scan --data '{...}'
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "earnings_trades.db"

_DDL = """
CREATE TABLE IF NOT EXISTS iron_fly_trades (
    order_id        TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    expiration      TEXT NOT NULL,
    short_strike    REAL,
    long_call_strike REAL,
    long_put_strike REAL,
    entry_credit    REAL,
    exit_debit      REAL,
    pnl             REAL,
    opened_at       REAL,
    closed_at       REAL
);

CREATE TABLE IF NOT EXISTS scan_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_date   TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    tier        TEXT,
    outcome     TEXT,
    reason      TEXT,
    logged_at   REAL
);

CREATE TABLE IF NOT EXISTS daily_summary (
    summary_date    TEXT PRIMARY KEY,
    positions_opened INTEGER,
    positions_closed INTEGER,
    net_pnl        REAL
);
"""


def cmd_init_db(args) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_DDL)
    conn.commit()
    conn.close()
    return {"ok": True, "db_path": str(DB_PATH)}


def cmd_get_open_positions(args) -> dict:
    raise NotImplementedError


def cmd_save_trade(args) -> dict:
    raise NotImplementedError


def cmd_save_close(args) -> dict:
    raise NotImplementedError


def cmd_log_scan(args) -> dict:
    raise NotImplementedError


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init_db")
    sub.add_parser("get_open_positions")

    p_save_trade = sub.add_parser("save_trade")
    p_save_trade.add_argument("--data", required=True)

    p_save_close = sub.add_parser("save_close")
    p_save_close.add_argument("--data", required=True)

    p_log_scan = sub.add_parser("log_scan")
    p_log_scan.add_argument("--data", required=True)

    args = parser.parse_args()
    dispatch = {
        "init_db": cmd_init_db,
        "get_open_positions": cmd_get_open_positions,
        "save_trade": cmd_save_trade,
        "save_close": cmd_save_close,
        "log_scan": cmd_log_scan,
    }
    result = dispatch[args.command](args)
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
