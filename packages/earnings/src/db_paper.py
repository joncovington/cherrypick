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
      "long_put_strike": F, "legs_json": "...", "entry_credit": F,
      "profile": "balanced", "quantity": N, "capital_at_risk": F, "entry_cost": F,
      "entry_context": {...}}'
  save_close --data '{"order_id": "...", "exit_debit": F, "pnl": F, "exit_cost": F}'
  get_open_legs --order_id X
  save_leg_close --data '{"order_id": "...", "leg_role": "...", "close_price": F}'
  log_scan --data '{"scan_date": "YYYY-MM-DD", "symbol": "...", "strategy": "iron_fly",
      "tier": "...", "outcome": "...", "reason": "...", "profile": "balanced"}'
  get_pnl_summary [--strategy X] [--profile X]

`legs` (optional array on save_trade, each `{leg_role, symbol, action, quantity}`) is for
strategies with independently-closeable legs (e.g. double_calendar's threatened-side close)
-- iron fly never passes it, so it never gets `trade_legs` rows. A trade's `trades.closed_at`
stays NULL until every one of its legs is closed via save_leg_close and save_close is called
for the position as a whole.

`profile` (defaults to 'default') tags which named risk profile / test book opened a trade
or produced a scan_log row (see docs/paper-trading-profiles.md) -- lets many isolated books
share this one file without ever mixing their P&L or candidate history. `quantity` and
`capital_at_risk` come from sizing.compute_position_size; `entry_cost`/`exit_cost` come from
costs.py's tastytrade fee+slippage model (kept separate from entry_credit/exit_debit/pnl so
cost impact is analyzable on its own). `entry_context` is a small JSON blob of the market
conditions at entry (iv_rv_ratio, dispersion, skew, winrate_sample_size) for regime slicing
in strategy_metrics.py -- stored verbatim, never parsed by db_paper.py itself.
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
    closed_at       REAL,
    profile         TEXT NOT NULL DEFAULT 'default',
    quantity        INTEGER,
    capital_at_risk REAL,
    entry_cost      REAL,
    exit_cost       REAL,
    entry_context   TEXT,
    entry_iv        REAL,
    exit_iv         REAL
);

CREATE TABLE IF NOT EXISTS trade_legs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    TEXT NOT NULL,
    leg_role    TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    action      TEXT NOT NULL,
    quantity    INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    close_price REAL,
    closed_at   REAL,
    UNIQUE(order_id, leg_role)
);

CREATE TABLE IF NOT EXISTS scan_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_date   TEXT NOT NULL,
    strategy    TEXT NOT NULL DEFAULT 'iron_fly',
    symbol      TEXT NOT NULL,
    tier        TEXT,
    outcome     TEXT,
    reason      TEXT,
    logged_at   REAL,
    profile     TEXT NOT NULL DEFAULT 'default'
);

CREATE TABLE IF NOT EXISTS daily_summary (
    summary_date    TEXT PRIMARY KEY,
    positions_opened INTEGER,
    positions_closed INTEGER,
    net_pnl        REAL
);
"""

# Idempotent migration for databases created before profile/sizing/cost attribution
# existed (CREATE TABLE IF NOT EXISTS is a no-op on an already-existing table, so new
# columns never appear there without this). Each entry: (table, column, ADD COLUMN clause).
_MIGRATIONS = [
    ("trades", "profile", "ALTER TABLE trades ADD COLUMN profile TEXT NOT NULL DEFAULT 'default'"),
    ("trades", "quantity", "ALTER TABLE trades ADD COLUMN quantity INTEGER"),
    ("trades", "capital_at_risk", "ALTER TABLE trades ADD COLUMN capital_at_risk REAL"),
    ("trades", "entry_cost", "ALTER TABLE trades ADD COLUMN entry_cost REAL"),
    ("trades", "exit_cost", "ALTER TABLE trades ADD COLUMN exit_cost REAL"),
    ("trades", "entry_context", "ALTER TABLE trades ADD COLUMN entry_context TEXT"),
    ("trades", "entry_iv", "ALTER TABLE trades ADD COLUMN entry_iv REAL"),
    ("trades", "exit_iv", "ALTER TABLE trades ADD COLUMN exit_iv REAL"),
    ("scan_log", "profile", "ALTER TABLE scan_log ADD COLUMN profile TEXT NOT NULL DEFAULT 'default'"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, alter_sql in _MIGRATIONS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            conn.execute(alter_sql)
    conn.commit()


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    _migrate(conn)
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

    entry_context = spec.get("entry_context")
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO trades "
            "(order_id, strategy, symbol, expiration, short_strike, long_call_strike, "
            " long_put_strike, legs_json, entry_credit, opened_at, profile, quantity, "
            " capital_at_risk, entry_cost, entry_context, entry_iv) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                spec.get("profile", "default"),
                spec.get("quantity"),
                spec.get("capital_at_risk"),
                spec.get("entry_cost"),
                json.dumps(entry_context) if entry_context is not None else None,
                spec.get("entry_iv"),
            ),
        )
        for leg in spec.get("legs", []):
            conn.execute(
                "INSERT INTO trade_legs (order_id, leg_role, symbol, action, quantity) "
                "VALUES (?, ?, ?, ?, ?)",
                (spec["order_id"], leg["leg_role"], leg["symbol"], leg["action"], leg["quantity"]),
            )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        return {"ok": False, "error": f"save_trade failed: {exc}"}
    finally:
        conn.close()
    return {"ok": True, "order_id": spec["order_id"]}


def cmd_get_open_legs(args) -> dict:
    order_id = args.order_id
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM trade_legs WHERE order_id = ? AND status = 'open' ORDER BY leg_role",
            (order_id,),
        ).fetchall()
    finally:
        conn.close()
    return {"ok": True, "order_id": order_id, "legs": [dict(r) for r in rows]}


def cmd_save_leg_close(args) -> dict:
    spec = json.loads(args.data)
    required = ("order_id", "leg_role")
    missing = [k for k in required if not spec.get(k)]
    if missing:
        return {"ok": False, "error": f"missing required field(s): {', '.join(missing)}"}

    conn = _conn()
    try:
        cur = conn.execute(
            "UPDATE trade_legs SET status = 'closed', close_price = ?, closed_at = ? "
            "WHERE order_id = ? AND leg_role = ? AND status = 'open'",
            (
                spec.get("close_price"),
                spec.get("closed_at", time.time()),
                spec["order_id"],
                spec["leg_role"],
            ),
        )
        conn.commit()
        if cur.rowcount == 0:
            return {"ok": False, "error": f"no open leg found for order_id={spec['order_id']} leg_role={spec['leg_role']}"}
    finally:
        conn.close()
    return {"ok": True, "order_id": spec["order_id"], "leg_role": spec["leg_role"]}


def cmd_save_close(args) -> dict:
    spec = json.loads(args.data)
    order_id = spec.get("order_id")
    if not order_id:
        return {"ok": False, "error": "missing required field: order_id"}

    conn = _conn()
    try:
        cur = conn.execute(
            "UPDATE trades SET exit_debit = ?, pnl = ?, closed_at = ?, exit_cost = ?, exit_iv = ? "
            "WHERE order_id = ?",
            (
                spec.get("exit_debit"),
                spec.get("pnl"),
                spec.get("closed_at", time.time()),
                spec.get("exit_cost"),
                spec.get("exit_iv"),
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
            "INSERT INTO scan_log (scan_date, strategy, symbol, tier, outcome, reason, logged_at, profile) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                spec["scan_date"],
                spec.get("strategy", "iron_fly"),
                spec["symbol"],
                spec.get("tier"),
                spec.get("outcome"),
                spec.get("reason"),
                spec.get("logged_at", time.time()),
                spec.get("profile", "default"),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def cmd_get_pnl_summary(args) -> dict:
    strategy = getattr(args, "strategy", None)
    profile = getattr(args, "profile", None)
    conn = _conn()
    try:
        query = "SELECT * FROM trades WHERE closed_at IS NOT NULL"
        params: list = []
        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
        if profile:
            query += " AND profile = ?"
            params.append(profile)
        rows = conn.execute(query + " ORDER BY closed_at", params).fetchall()
    finally:
        conn.close()

    closed = [dict(r) for r in rows]
    pnls = [r["pnl"] for r in closed if r["pnl"] is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    by_strategy: dict[str, list[float]] = {}
    by_profile: dict[str, list[float]] = {}
    for r in closed:
        if r["pnl"] is not None:
            by_strategy.setdefault(r["strategy"], []).append(r["pnl"])
            by_profile.setdefault(r["profile"], []).append(r["pnl"])

    return {
        "ok": True,
        "strategy_filter": strategy,
        "profile_filter": profile,
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
        "by_profile": {
            p: {"trades": len(vals), "total_pnl": sum(vals), "avg_pnl": sum(vals) / len(vals)}
            for p, vals in by_profile.items()
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
    p_pnl.add_argument("--profile", default=None)

    p_save_trade = sub.add_parser("save_trade")
    p_save_trade.add_argument("--data", required=True)

    p_save_close = sub.add_parser("save_close")
    p_save_close.add_argument("--data", required=True)

    p_get_open_legs = sub.add_parser("get_open_legs")
    p_get_open_legs.add_argument("--order_id", required=True)

    p_save_leg_close = sub.add_parser("save_leg_close")
    p_save_leg_close.add_argument("--data", required=True)

    p_log_scan = sub.add_parser("log_scan")
    p_log_scan.add_argument("--data", required=True)

    args = parser.parse_args()
    dispatch = {
        "init_db": cmd_init_db,
        "get_open_positions": cmd_get_open_positions,
        "save_trade": cmd_save_trade,
        "save_close": cmd_save_close,
        "get_open_legs": cmd_get_open_legs,
        "save_leg_close": cmd_save_leg_close,
        "log_scan": cmd_log_scan,
        "get_pnl_summary": cmd_get_pnl_summary,
    }
    result = dispatch[args.command](args)
    json.dump(result, sys.stdout, default=str)


if __name__ == "__main__":
    main()
