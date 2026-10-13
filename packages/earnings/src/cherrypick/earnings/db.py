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

`open_leg_symbols` (added 2026-09-17) is the flat streamer-symbol set the market-data producer
subscribes from, the same table `db_paper.py` keeps; `save_trade` fills it from the order's legs
(see `leg_streamer_symbols`) and `save_close` clears it, so a hand-recorded live position is
declared to the streamer by the act of recording it, with no extra step for the human to forget.

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
from cherrypick.core import streamcache as _streamcache

from cherrypick.earnings import ledger_verbs as _verbs
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
    arm             TEXT NOT NULL DEFAULT 'default',
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

-- The streamer symbols of every open position's legs, flat -- the same table db_paper.py keeps, so
-- stream_request.py's ONE leg query runs unchanged against either ledger. Added 2026-09-17: until
-- then the live ledger had no such table, so a live position's wings and back months were never
-- declared to the producer and priced off frozen quotes the moment spot left the ATM window.
-- Written by save_trade from the order's legs, cleared by save_close; the query also joins on
-- trades.closed_at, so a close that failed to clear its rows cannot pin a dead subscription.
CREATE TABLE IF NOT EXISTS open_leg_symbols (
    order_id        TEXT NOT NULL,
    streamer_symbol TEXT NOT NULL,
    PRIMARY KEY (order_id, streamer_symbol)
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
    arm            TEXT NOT NULL DEFAULT 'default'
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
    arm            TEXT NOT NULL DEFAULT 'default',
    UNIQUE(scan_date, symbol, arm)
);
"""

# Schema-parity migration with db_paper.py (see that module's own _MIGRATIONS docstring) --
# kept identical so the two databases' trades/scan_log tables never drift apart, even though
# live trading doesn't use named profiles/sizing/cost attribution today.
_MIGRATIONS = [
    ("trades", "arm", "ALTER TABLE trades ADD COLUMN arm TEXT NOT NULL DEFAULT 'default'"),
    ("trades", "quantity", "ALTER TABLE trades ADD COLUMN quantity INTEGER"),
    ("trades", "capital_at_risk", "ALTER TABLE trades ADD COLUMN capital_at_risk REAL"),
    ("trades", "entry_cost", "ALTER TABLE trades ADD COLUMN entry_cost REAL"),
    ("trades", "exit_cost", "ALTER TABLE trades ADD COLUMN exit_cost REAL"),
    ("trades", "entry_context", "ALTER TABLE trades ADD COLUMN entry_context TEXT"),
    ("trades", "entry_iv", "ALTER TABLE trades ADD COLUMN entry_iv REAL"),
    ("trades", "exit_iv", "ALTER TABLE trades ADD COLUMN exit_iv REAL"),
    ("scan_log", "arm", "ALTER TABLE scan_log ADD COLUMN arm TEXT NOT NULL DEFAULT 'default'"),
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


# The tables carrying the arm column (`profile` until 2026-09-24).
_ARM_TABLES = ("trades", "scan_log", "entry_reviews")


_arm_of = _verbs.arm_of


def _conn() -> sqlite3.Connection:
    return _verbs.open_ledger(DB_PATH, _DDL, _migrate, _ARM_TABLES)


def cmd_init_db(args) -> dict:
    return _verbs.init_db(_conn, _DDL, DB_PATH)


def cmd_get_open_positions(args) -> dict:
    return _verbs.get_open_positions(_conn)


def leg_streamer_symbols(spec: dict) -> list[str]:
    """The streamer symbols of a save_trade spec's legs, from ``legs_json`` and ``legs`` alike.

    A leg that already carries ``streamer_symbol`` (the chain's own, as the paper harness stamps)
    keeps it; one that carries only the OCC ``symbol`` the order was placed with is converted through
    ``cherrypick.core.streamcache.occ_to_streamer_symbol``, which is pinned to the SDK's converter --
    the live path is a human running ``tt.py execute_trade --live`` and then ``save_trade`` with the
    order's legs verbatim, and asking that path to also copy a chain field by hand is how the paper
    table sat empty for 64 trades. Unconvertible legs are dropped, never raised on: a subscription
    record must not fail the trade that was already placed.
    """
    legs: list = []
    for raw in (spec.get("legs_json"), spec.get("legs")):
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = None
        if isinstance(raw, list):
            legs.extend(leg for leg in raw if isinstance(leg, dict))
    out: set[str] = set()
    for leg in legs:
        sym = leg.get("streamer_symbol")
        if not (isinstance(sym, str) and sym.strip()):
            occ = leg.get("symbol")
            sym = _streamcache.occ_to_streamer_symbol(occ) if isinstance(occ, str) else ""
        if isinstance(sym, str) and sym.strip():
            out.add(sym.strip())
    return sorted(out)


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
            " long_put_strike, legs_json, entry_credit, opened_at, arm, quantity, "
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
                _arm_of(spec),
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
        # Declare the legs to the producer (stream_request.py's query reads this table). In the same
        # transaction as the trade row, so the ledger can never hold a live position whose legs it
        # forgot to declare -- the 2026-09-17 gap this table closes.
        conn.executemany(
            "INSERT OR IGNORE INTO open_leg_symbols (order_id, streamer_symbol) VALUES (?, ?)",
            [(spec["order_id"], sym) for sym in leg_streamer_symbols(spec)],
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        return {"ok": False, "error": f"save_trade failed: {exc}"}
    finally:
        conn.close()
    return {"ok": True, "order_id": spec["order_id"]}


def cmd_get_open_legs(args) -> dict:
    return _verbs.get_open_legs(_conn, args)


def cmd_save_leg_close(args) -> dict:
    return _verbs.save_leg_close(_conn, args)


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
        # The join on trades.closed_at already drops these from the producer's view; clearing them
        # too keeps the table an honest "open legs" set rather than a history.
        conn.execute("DELETE FROM open_leg_symbols WHERE order_id = ?", (order_id,))
        conn.commit()
        if cur.rowcount == 0:
            return {"ok": False, "error": f"no open trade found for order_id {order_id}"}
    finally:
        conn.close()
    return {"ok": True, "order_id": order_id}


def cmd_log_scan(args) -> dict:
    return _verbs.log_scan(_conn, args)


_ENTRY_REVIEW_COLUMNS = _verbs._ENTRY_REVIEW_COLUMNS


_entry_review_values = _verbs.entry_review_values


def cmd_save_entry_review(args) -> dict:
    return _verbs.save_entry_review(_conn, args)


def cmd_get_entry_reviews(args) -> dict:
    return _verbs.get_entry_reviews(_conn, args)


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
