"""SQLite persistence for EarningsAgent's PAPER TRADING simulation.

A deliberately separate database (data/paper_trades.db) and separate CLI
from db.py/earnings_trades.db -- paper and real trade data must never be
queryable through the same connection or file, so there is no --paper
flag on db.py and no shared code path that could blend the two.

Schema is strategy-agnostic (see db.py's own docstring for the rationale):
`trades.strategy` identifies which strategy opened a position, and
`legs_json` holds that strategy's actual order legs verbatim.

Commands:
  init_db
  get_open_positions
  save_trade --data '{"order_id": "...", "strategy": "iron_fly", "symbol": "...",
      "expiration": "YYYY-MM-DD", "short_strike": F, "long_call_strike": F,
      "long_put_strike": F, "legs_json": "...", "entry_credit": F}'
  save_close --data '{"order_id": "...", "exit_debit": F, "pnl": F}'
  log_scan --data '{"scan_date": "YYYY-MM-DD", "symbol": "...", "strategy": "iron_fly",
      "tier": "...", "outcome": "...", "reason": "..."}'
  get_pnl_summary [--strategy X]
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "paper_trades.db"

_DDL = """
CREATE TABLE IF NOT EXISTS trades (
    order_id        TEXT PRIMARY KEY,
    strategy        TEXT NOT NULL DEFAULT 'iron_fly',
    symbol          TEXT NOT NULL,
    expiration      TEXT NOT NULL,
    short_strike    REAL,
    long_call_strike REAL,
    long_put_strike REAL,
    legs_json       TEXT,
    entry_credit    REAL,
    exit_debit      REAL,
    pnl             REAL,
    opened_at       REAL,
    closed_at       REAL
);

CREATE TABLE IF NOT EXISTS scan_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_date   TEXT NOT NULL,
    strategy    TEXT NOT NULL DEFAULT 'iron_fly',
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


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def cmd_init_db(args) -> dict:
    conn = _conn()
    conn.executescript(_DDL)
    conn.commit()
    conn.close()
    return {"ok": True, "db_path": str(DB_PATH)}


def cmd_get_open_positions(args) -> dict:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM trades WHERE closed_at IS NULL ORDER BY opened_at"
        ).fetchall()
    finally:
        conn.close()
    return {"ok": True, "positions": [dict(r) for r in rows]}


def cmd_save_trade(args) -> dict:
    spec = json.loads(args.data)
    required = ("order_id", "symbol", "expiration")
    missing = [k for k in required if not spec.get(k)]
    if missing:
        return {"ok": False, "error": f"missing required field(s): {', '.join(missing)}"}

    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO trades "
            "(order_id, strategy, symbol, expiration, short_strike, long_call_strike, "
            " long_put_strike, legs_json, entry_credit, opened_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                spec["order_id"],
                spec.get("strategy", "iron_fly"),
                spec["symbol"],
                spec["expiration"],
                spec.get("short_strike"),
                spec.get("long_call_strike"),
                spec.get("long_put_strike"),
                spec.get("legs_json"),
                spec.get("entry_credit"),
                spec.get("opened_at", time.time()),
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        return {"ok": False, "error": f"save_trade failed: {exc}"}
    finally:
        conn.close()
    return {"ok": True, "order_id": spec["order_id"]}


def cmd_save_close(args) -> dict:
    spec = json.loads(args.data)
    order_id = spec.get("order_id")
    if not order_id:
        return {"ok": False, "error": "missing required field: order_id"}

    conn = _conn()
    try:
        cur = conn.execute(
            "UPDATE trades SET exit_debit = ?, pnl = ?, closed_at = ? "
            "WHERE order_id = ?",
            (
                spec.get("exit_debit"),
                spec.get("pnl"),
                spec.get("closed_at", time.time()),
                order_id,
            ),
        )
        conn.commit()
        if cur.rowcount == 0:
            return {"ok": False, "error": f"no open trade found for order_id {order_id}"}
    finally:
        conn.close()
    return {"ok": True, "order_id": order_id}


def cmd_log_scan(args) -> dict:
    spec = json.loads(args.data)
    required = ("scan_date", "symbol")
    missing = [k for k in required if not spec.get(k)]
    if missing:
        return {"ok": False, "error": f"missing required field(s): {', '.join(missing)}"}

    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO scan_log (scan_date, strategy, symbol, tier, outcome, reason, logged_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                spec["scan_date"],
                spec.get("strategy", "iron_fly"),
                spec["symbol"],
                spec.get("tier"),
                spec.get("outcome"),
                spec.get("reason"),
                spec.get("logged_at", time.time()),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def cmd_get_pnl_summary(args) -> dict:
    strategy = getattr(args, "strategy", None)
    conn = _conn()
    try:
        if strategy:
            rows = conn.execute(
                "SELECT * FROM trades WHERE closed_at IS NOT NULL AND strategy = ? ORDER BY closed_at",
                (strategy,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trades WHERE closed_at IS NOT NULL ORDER BY closed_at"
            ).fetchall()
    finally:
        conn.close()

    closed = [dict(r) for r in rows]
    pnls = [r["pnl"] for r in closed if r["pnl"] is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    by_strategy: dict[str, list[float]] = {}
    for r in closed:
        if r["pnl"] is not None:
            by_strategy.setdefault(r["strategy"], []).append(r["pnl"])

    return {
        "ok": True,
        "strategy_filter": strategy,
        "total_trades": len(closed),
        "total_pnl": sum(pnls) if pnls else 0.0,
        "avg_pnl": (sum(pnls) / len(pnls)) if pnls else None,
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": (len(wins) / len(pnls)) if pnls else None,
        "avg_win": (sum(wins) / len(wins)) if wins else None,
        "avg_loss": (sum(losses) / len(losses)) if losses else None,
        "by_strategy": {
            s: {"trades": len(vals), "total_pnl": sum(vals), "avg_pnl": sum(vals) / len(vals)}
            for s, vals in by_strategy.items()
        },
        "trades": closed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init_db")
    sub.add_parser("get_open_positions")

    p_pnl = sub.add_parser("get_pnl_summary")
    p_pnl.add_argument("--strategy", default=None)

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
        "get_pnl_summary": cmd_get_pnl_summary,
    }
    result = dispatch[args.command](args)
    json.dump(result, sys.stdout, default=str)


if __name__ == "__main__":
    main()
