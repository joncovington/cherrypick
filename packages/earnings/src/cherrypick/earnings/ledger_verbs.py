"""The ledger verbs the live (`db.py`) and paper (`db_paper.py`) ledgers share, in one copy.

The two modules carried these ten functions byte for byte -- and textually identical is not the
same as identical: each copy reached its OWN module's `DB_PATH`, DDL, migrations and arm tables
through module globals. So every verb here takes the module's connection factory (or those pieces)
as an argument, and each ledger module keeps its own `cmd_*` names as one-line wrappers. A test
that patches `db_paper.DB_PATH` (or `_conn`) still reaches every verb, because the wrapper resolves
the module global at call time.

`_ENTRY_REVIEW_COLUMNS` and `entry_review_values` live here because the live and paper
`entry_reviews` tables are kept identical on purpose (schema parity): one tuple cannot drift.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from cherrypick.core import config as _cfg
from cherrypick.core import db as _db

ConnFactory = Callable[[], sqlite3.Connection]


def arm_of(spec: dict, default: str | None = "default") -> str | None:
    """The arm a save spec is tagged with. The key was `profile` until 2026-09-24 (the column's old
    name) and is `arm` after it; a caller still sending `profile` is read, never dropped -- dropping
    it would file the row under `default`, i.e. under the wrong arm, with nothing raised."""
    return _cfg.first_present(spec, "arm", "profile", default=default)


def open_ledger(
    db_path: Path | str, ddl: str, migrate: Callable[[sqlite3.Connection], None], arm_tables
) -> sqlite3.Connection:
    """Open a ledger for writing: mkdir + row_factory (core.db), refuse a pre-rename ledger, run
    the DDL and the module's migrations."""
    conn = _db.connect(db_path)  # mkdir parent + row_factory=Row (see cherrypick.core.db)
    # Before the DDL and the migrations, which now name `arm`: on a ledger that still says
    # `profile` they would ADD `arm` beside it and split every table's history from its new rows.
    _db.refuse_pre_rename(conn, arm_tables, "profile")
    conn.executescript(ddl)
    migrate(conn)
    return conn


def init_db(conn_factory: ConnFactory, ddl: str, db_path) -> dict:
    conn = conn_factory()
    conn.executescript(ddl)
    conn.commit()
    conn.close()
    return {"ok": True, "db_path": str(db_path)}


def get_open_positions(conn_factory: ConnFactory) -> dict:
    conn = conn_factory()
    try:
        rows = conn.execute("SELECT * FROM trades WHERE closed_at IS NULL ORDER BY opened_at").fetchall()
    finally:
        conn.close()
    return {"ok": True, "positions": [dict(r) for r in rows]}


def get_open_legs(conn_factory: ConnFactory, args) -> dict:
    order_id = args.order_id
    conn = conn_factory()
    try:
        rows = conn.execute(
            "SELECT * FROM trade_legs WHERE order_id = ? AND status = 'open' ORDER BY leg_role",
            (order_id,),
        ).fetchall()
    finally:
        conn.close()
    return {"ok": True, "order_id": order_id, "legs": [dict(r) for r in rows]}


def save_leg_close(conn_factory: ConnFactory, args) -> dict:
    spec = json.loads(args.data)
    required = ("order_id", "leg_role")
    missing = [k for k in required if not spec.get(k)]
    if missing:
        return {"ok": False, "error": f"missing required field(s): {', '.join(missing)}"}

    conn = conn_factory()
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


def log_scan(conn_factory: ConnFactory, args) -> dict:
    spec = json.loads(args.data)
    required = ("scan_date", "symbol")
    missing = [k for k in required if not spec.get(k)]
    if missing:
        return {"ok": False, "error": f"missing required field(s): {', '.join(missing)}"}

    conn = conn_factory()
    try:
        conn.execute(
            "INSERT INTO scan_log (scan_date, strategy, symbol, tier, outcome, reason, stage, "
            "reject_details, logged_at, arm) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                arm_of(spec),
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
    "arm",
)


def entry_review_values(spec: dict) -> tuple:
    """Positional values for `_ENTRY_REVIEW_COLUMNS`."""
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
        arm_of(spec),
    )


def save_entry_review(conn_factory: ConnFactory, args) -> dict:
    """Upsert one per-symbol entry-review record -- the data reviewed for a symbol during an entry
    scan plus the chosen/rejected decision (see scanner.build_entry_review_spec for the field set).
    Idempotent on (scan_date, symbol, arm) so a re-run of the scan overwrites. Read by scout's
    read-only earnings page."""
    spec = json.loads(args.data)
    for req in ("scan_date", "symbol"):
        if not spec.get(req):
            return {"ok": False, "error": f"missing required field: {req}"}
    conn = conn_factory()
    try:
        cols = ", ".join(_ENTRY_REVIEW_COLUMNS)
        placeholders = ", ".join("?" for _ in _ENTRY_REVIEW_COLUMNS)
        updates = ", ".join(
            f"{c}=excluded.{c}" for c in _ENTRY_REVIEW_COLUMNS if c not in ("scan_date", "symbol", "arm")
        )
        conn.execute(
            f"INSERT INTO entry_reviews ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(scan_date, symbol, arm) DO UPDATE SET {updates}",
            entry_review_values(spec),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "scan_date": spec["scan_date"], "symbol": spec["symbol"]}


def get_entry_reviews(conn_factory: ConnFactory, args) -> dict:
    """Entry reviews for a scan date (default: the most recent scan on or before --date). Ordered
    selected-first, then by symbol."""
    conn = conn_factory()
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
