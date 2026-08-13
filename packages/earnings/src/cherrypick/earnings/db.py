"""SQLite persistence for EarningsAgent's real (non-paper) trades.

Schema is strategy-agnostic: `trades.strategy` identifies which strategy
opened a position (e.g. "iron_fly"), and `legs_json` holds that
strategy's actual order legs verbatim, so a future strategy with a
different leg count/shape needs no schema change. `short_strike`/
`long_call_strike`/`long_put_strike` remain as convenience columns
specific to symmetric-wing strategies like iron fly -- left NULL for
strategies that don't have that shape.

Commands (see CLAUDE.md's Database section):
  init_db
  get_open_positions
  save_trade --data '{"order_id": "...", "strategy": "iron_fly", "symbol": "...",
      "expiration": "YYYY-MM-DD", "short_strike": F, "long_call_strike": F,
      "long_put_strike": F, "legs_json": "...", "entry_credit": F}'
  save_close --data '{"order_id": "...", "exit_debit": F, "pnl": F}'
  get_open_legs --order_id X
  save_leg_close --data '{"order_id": "...", "leg_role": "...", "close_price": F}'
  log_scan --data '{"scan_date": "YYYY-MM-DD", "symbol": "...", "strategy": "iron_fly",
      "tier": "...", "outcome": "...", "reason": "..."}'
  save_entry_review --data '{"scan_date": "...", "symbol": "...", ...}' (see
      scanner.build_entry_review_spec -- the reviewed metric vector + accept/reject decision
      for one symbol, whether selected or not)
  get_entry_reviews [--date YYYY-MM-DD] [--scan_date YYYY-MM-DD]

`legs` (optional array on save_trade, each `{leg_role, symbol, action, quantity}`) is for
strategies with independently-closeable legs (e.g. double_calendar's threatened-side close)
-- iron fly never passes it, so it never gets `trade_legs` rows. A trade's `trades.closed_at`
stays NULL until every one of its legs is closed via save_leg_close and save_close is called
for the position as a whole.

`profile`/`quantity`/`capital_at_risk`/`entry_cost`/`exit_cost`/`entry_context`/`entry_iv`/
`exit_iv` exist for schema parity with db_paper.py's paper-mode profile testing (see
docs/strat-test-portfolios.md) -- live trading doesn't select a profile today, so these
default to 'default'/NULL, but the two databases' `trades`/`scan_log` tables never drift
apart as a result.
"""

import argparse
import json
import sqlite3
import sys
import time

# Make `import paths` resolve when this file is imported (not run as the __main__ script, which
# gets its own directory on sys.path automatically) -- mirrors credentials.py's self-insert.
from cherrypick.core import db as _db

from cherrypick.earnings import paths as _paths

# Resolved from the shared cherrypick data home (~/.cherrypick/data/earnings by default, or
# EARNINGS_DATA_DIR) so this checkout and the orchestrator read/write the same ledger. See paths.py.
DB_PATH = _paths.live_db_path()

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
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_date      TEXT NOT NULL,
    strategy       TEXT NOT NULL DEFAULT 'iron_fly',
    symbol         TEXT NOT NULL,
    tier           TEXT,
    outcome        TEXT,
    reason         TEXT,
    stage          TEXT NOT NULL DEFAULT 'screen',
    reject_details TEXT,
    logged_at      REAL,
    profile        TEXT NOT NULL DEFAULT 'default'
);

CREATE TABLE IF NOT EXISTS daily_summary (
    summary_date    TEXT PRIMARY KEY,
    positions_opened INTEGER,
    positions_closed INTEGER,
    net_pnl        REAL
);

CREATE TABLE IF NOT EXISTS entry_reviews (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_date      TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    timing         TEXT,
    timing_assumed INTEGER,
    strategy       TEXT,
    price          REAL,
    volume         REAL,
    winrate        REAL,
    winrate_sample INTEGER,
    iv_rv_ratio    REAL,
    iv_rv_source   TEXT,
    term_structure REAL,
    market_cap     REAL,
    expected_move  REAL,
    expected_move_pct       REAL,
    combined_open_interest  REAL,
    combined_option_volume  REAL,
    bid_ask_spread_pct      REAL,
    net_combo_spread_pct    REAL,
    avg_actual_move_pct     REAL,
    move_dispersion_pct     REAL,
    max_actual_move_pct     REAL,
    implied_vs_avg_actual   REAL,
    move_tail_veto INTEGER,
    iv_rank        REAL,
    iv_percentile  REAL,
    composite_score REAL,
    best_tier      TEXT,
    selected       INTEGER NOT NULL DEFAULT 0,
    reason         TEXT,
    criteria_json  TEXT,
    logged_at      REAL,
    profile        TEXT NOT NULL DEFAULT 'default',
    UNIQUE(scan_date, symbol, profile)
);
"""

# Schema-parity migration with db_paper.py (see that module's own _MIGRATIONS docstring) --
# kept identical so the two databases' trades/scan_log tables never drift apart, even though
# live trading doesn't use named profiles/sizing/cost attribution today.
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
    # See db_paper.py's copies of these two for the full reasoning: 'screen' rows carry the
    # accept/reject verdict, 'execution' rows what happened to an accepted candidate afterwards;
    # reject_details carries each reason's measured value and the threshold it missed.
    ("scan_log", "stage", "ALTER TABLE scan_log ADD COLUMN stage TEXT NOT NULL DEFAULT 'screen'"),
    ("scan_log", "reject_details", "ALTER TABLE scan_log ADD COLUMN reject_details TEXT"),
    # Nullable and deliberately not backfilled -- NULL means "predates the distinction", which is
    # the truth; see db_paper.py's copy of this migration for the full reasoning.
    ("entry_reviews", "timing_assumed", "ALTER TABLE entry_reviews ADD COLUMN timing_assumed INTEGER"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    _db.apply_additive_migrations(conn, _MIGRATIONS)


def _conn() -> sqlite3.Connection:
    conn = _db.connect(DB_PATH)  # mkdir parent + row_factory=Row (see cherrypick.core.db)
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
        rows = conn.execute("SELECT * FROM trades WHERE closed_at IS NULL ORDER BY opened_at").fetchall()
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
            return {
                "ok": False,
                "error": f"no open leg found for order_id={spec['order_id']} leg_role={spec['leg_role']}",
            }
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
            "INSERT INTO scan_log (scan_date, strategy, symbol, tier, outcome, reason, stage, "
            "reject_details, logged_at, profile) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                spec["scan_date"],
                spec.get("strategy", "iron_fly"),
                spec["symbol"],
                spec.get("tier"),
                spec.get("outcome"),
                spec.get("reason"),
                spec.get("stage", "screen"),
                json.dumps(spec["reject_details"]) if spec.get("reject_details") else None,
                spec.get("logged_at", time.time()),
                spec.get("profile", "default"),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


_ENTRY_REVIEW_COLUMNS = (
    "scan_date",
    "symbol",
    "timing",
    "timing_assumed",
    "strategy",
    "price",
    "volume",
    "winrate",
    "winrate_sample",
    "iv_rv_ratio",
    "iv_rv_source",
    "term_structure",
    "market_cap",
    "expected_move",
    "expected_move_pct",
    "combined_open_interest",
    "combined_option_volume",
    "bid_ask_spread_pct",
    "net_combo_spread_pct",
    "avg_actual_move_pct",
    "move_dispersion_pct",
    "max_actual_move_pct",
    "implied_vs_avg_actual",
    "move_tail_veto",
    "iv_rank",
    "iv_percentile",
    "composite_score",
    "selected",
    "reason",
    "criteria_json",
    "logged_at",
    "profile",
)


def _entry_review_values(spec: dict) -> tuple:
    """Positional values for _ENTRY_REVIEW_COLUMNS -- byte-identical to db_paper.py's own
    helper of the same name, kept in step so the live and paper entry_reviews tables never
    drift apart (same schema-parity discipline as trades/scan_log elsewhere in this file)."""
    crit = spec.get("criteria") if spec.get("criteria") is not None else spec.get("criteria_json")
    return (
        spec["scan_date"],
        spec["symbol"],
        spec.get("timing"),
        (1 if spec.get("timing_assumed") else (0 if spec.get("timing_assumed") is not None else None)),
        spec.get("strategy"),
        spec.get("price"),
        spec.get("volume"),
        spec.get("winrate"),
        spec.get("winrate_sample"),
        spec.get("iv_rv_ratio"),
        spec.get("iv_rv_source"),
        spec.get("term_structure"),
        spec.get("market_cap"),
        spec.get("expected_move"),
        spec.get("expected_move_pct"),
        spec.get("combined_open_interest"),
        spec.get("combined_option_volume"),
        spec.get("bid_ask_spread_pct"),
        spec.get("net_combo_spread_pct"),
        spec.get("avg_actual_move_pct"),
        spec.get("move_dispersion_pct"),
        spec.get("max_actual_move_pct"),
        spec.get("implied_vs_avg_actual"),
        (1 if spec.get("move_tail_veto") else (0 if spec.get("move_tail_veto") is not None else None)),
        spec.get("iv_rank"),
        spec.get("iv_percentile"),
        spec.get("composite_score"),
        1 if spec.get("selected") else 0,
        spec.get("reason"),
        json.dumps(crit) if crit is not None else None,
        spec.get("logged_at", time.time()),
        spec.get("profile", "default"),
    )


def cmd_save_entry_review(args) -> dict:
    """Upsert one per-symbol entry-review record — the data reviewed for a symbol during an entry scan
    plus the chosen/rejected decision (see scanner.build_entry_review_spec for the field set and
    db_paper.py's identical command for the paper-side twin). Idempotent on (scan_date, symbol,
    profile) so a re-run of the scan overwrites. Read by scout's read-only earnings page."""
    spec = json.loads(args.data)
    for req in ("scan_date", "symbol"):
        if not spec.get(req):
            return {"ok": False, "error": f"missing required field: {req}"}
    conn = _conn()
    try:
        cols = ", ".join(_ENTRY_REVIEW_COLUMNS)
        placeholders = ", ".join("?" for _ in _ENTRY_REVIEW_COLUMNS)
        updates = ", ".join(
            f"{c}=excluded.{c}" for c in _ENTRY_REVIEW_COLUMNS if c not in ("scan_date", "symbol", "profile")
        )
        conn.execute(
            f"INSERT INTO entry_reviews ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(scan_date, symbol, profile) DO UPDATE SET {updates}",
            _entry_review_values(spec),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "scan_date": spec["scan_date"], "symbol": spec["symbol"]}


def cmd_get_entry_reviews(args) -> dict:
    """Entry reviews for a scan date (default: the most recent scan on or before --date). Ordered
    selected-first, then by symbol -- identical query shape to db_paper.py's own command."""
    conn = _conn()
    try:
        scan_date = getattr(args, "scan_date", None)
        if not scan_date:
            on_or_before = getattr(args, "date", None)
            q = "SELECT MAX(scan_date) FROM entry_reviews"
            params: list = []
            if on_or_before:
                q += " WHERE scan_date <= ?"
                params.append(on_or_before)
            row = conn.execute(q, params).fetchone()
            scan_date = row[0] if row else None
        if not scan_date:
            return {"ok": True, "scan_date": None, "reviews": []}
        rows = conn.execute(
            "SELECT * FROM entry_reviews WHERE scan_date = ? ORDER BY selected DESC, symbol", (scan_date,)
        ).fetchall()
    finally:
        conn.close()
    return {"ok": True, "scan_date": scan_date, "reviews": [dict(r) for r in rows]}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init_db")
    sub.add_parser("get_open_positions")

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

    p_save_rev = sub.add_parser("save_entry_review")
    p_save_rev.add_argument("--data", required=True)

    p_get_rev = sub.add_parser("get_entry_reviews")
    p_get_rev.add_argument("--date", default=None, help="Most recent scan on or before this session day")
    p_get_rev.add_argument("--scan_date", default=None, help="Exact scan date (overrides --date)")

    args = parser.parse_args()
    dispatch = {
        "init_db": cmd_init_db,
        "get_open_positions": cmd_get_open_positions,
        "save_trade": cmd_save_trade,
        "save_close": cmd_save_close,
        "get_open_legs": cmd_get_open_legs,
        "save_leg_close": cmd_save_leg_close,
        "log_scan": cmd_log_scan,
        "save_entry_review": cmd_save_entry_review,
        "get_entry_reviews": cmd_get_entry_reviews,
    }
    result = dispatch[args.command](args)
    json.dump(result, sys.stdout, default=str)


if __name__ == "__main__":
    main()
