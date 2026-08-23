"""The curve paper ledger: schema, additive migrations, and every writer.

One SQLite file, `~/.cherrypick/data/curve/paper_trades.db` — the filename is load-bearing:
`packages/review` and the advisor's fact pack resolve `<data home>/curve/paper_trades.db`
generically, so moving or renaming it silently removes this module from both.

`curve_regime` is the module's second product: one row per session, written whether or not any
book trades, carrying the ratio, its classification, the hook flag, and each quote's own age and
freshness verdict — the honest continuity the series exists for (rule 7 of the module's honesty
rules). A refused/unusable regime day is still a row (`usable = 0` with the refusal), the same
"refused, never zero" discipline `curve_marks` applies to a mark path.
"""

from __future__ import annotations

import os
import sqlite3

from cherrypick.core import db as _core_db
from cherrypick.core import ledgerstore as _ledgerstore

_SCHEMA = """
-- One row per position per book: one short call + one long call (the same expiration, always).
-- `position_id` = "<symbol>:<book>:<entry_session>". Entry context is stored as MEASURES, never
-- only as buckets — a threshold can be re-cut later, a bucket cannot.
CREATE TABLE IF NOT EXISTS curve_positions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id         TEXT NOT NULL UNIQUE,
    symbol              TEXT NOT NULL,
    book                TEXT NOT NULL,
    entry_session       TEXT NOT NULL,
    quantity            INTEGER NOT NULL DEFAULT 1,
    expiration           TEXT NOT NULL,
    short_strike        REAL NOT NULL,
    long_strike         REAL NOT NULL,
    entry_time          TEXT,
    entry_spot          REAL,
    entry_short_mid     REAL,
    entry_long_mid      REAL,
    entry_credit        REAL,
    entry_width         REAL,
    entry_max_loss      REAL,
    entry_credit_pct_of_width REAL,
    entry_short_delta   REAL,
    short_selected_by   TEXT,
    entry_dte           INTEGER,
    entry_ratio         REAL,
    entry_regime        TEXT,
    entry_hook          INTEGER,
    entry_cost          REAL,
    entry_slippage      REAL,
    advice_params       TEXT,
    exposure_ticks      INTEGER,
    status              TEXT NOT NULL DEFAULT 'open',
    exit_reason         TEXT,
    closed_at           TEXT,
    closed_session      TEXT,
    exit_value          REAL,
    exit_cost           REAL,
    exit_slippage       REAL,
    settlement_spot     REAL,
    itm_settlements     INTEGER,
    gross_pnl           REAL,
    fees                REAL,
    created_at          TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_curve_positions_session ON curve_positions(entry_session, book);
CREATE INDEX IF NOT EXISTS idx_curve_positions_status ON curve_positions(status);

-- Legs per position: short_call, long_call.
CREATE TABLE IF NOT EXISTS curve_legs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     TEXT NOT NULL,
    leg_role        TEXT NOT NULL,
    occ_symbol      TEXT NOT NULL,
    streamer_symbol TEXT NOT NULL,
    expiration      TEXT NOT NULL,
    strike          REAL NOT NULL,
    option_type     TEXT NOT NULL,
    action          TEXT NOT NULL,
    quantity        INTEGER NOT NULL DEFAULT 1,
    entry_bid       REAL,
    entry_ask       REAL,
    entry_mid       REAL,
    entry_iv        REAL,
    entry_delta     REAL,
    status          TEXT NOT NULL DEFAULT 'open',
    close_kind      TEXT,
    closed_at       TEXT,
    close_bid       REAL,
    close_ask       REAL,
    close_value     REAL,
    created_at      TEXT,
    updated_at      TEXT,
    UNIQUE(position_id, leg_role)
);
CREATE INDEX IF NOT EXISTS idx_curve_legs_status ON curve_legs(status, expiration);

-- THE per-tick mark path — the middle path the plan's backtesting section describes: forward-
-- recorded now, replayable later (different profit takes, different flip thresholds, exact
-- pairing) without ever having to imagine a fill. Refusals are rows too.
CREATE TABLE IF NOT EXISTS curve_marks (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id        TEXT NOT NULL,
    leg_role           TEXT,
    marked_at          REAL NOT NULL,
    session_date       TEXT NOT NULL,
    bid                REAL,
    ask                REAL,
    mid                REAL,
    delta              REAL,
    iv                 REAL,
    spot               REAL,
    close_cost         REAL,
    short_tv           REAL,
    assignment_exposed INTEGER,
    quote_age_s        REAL,
    usable             INTEGER NOT NULL DEFAULT 0,
    refusal            TEXT
);
CREATE INDEX IF NOT EXISTS idx_curve_marks_position ON curve_marks(position_id, marked_at);
CREATE INDEX IF NOT EXISTS idx_curve_marks_session ON curve_marks(session_date);

-- The daily regime series — the module's second product. Written EVERY session, traded or not.
CREATE TABLE IF NOT EXISTS curve_regime (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date      TEXT NOT NULL UNIQUE,
    tick            TEXT NOT NULL,
    recorded_at     TEXT,
    ratio           REAL,
    regime          TEXT,
    hook            INTEGER,
    vix             REAL,
    vix3m           REAL,
    vix_age_s       REAL,
    vix3m_age_s     REAL,
    usable          INTEGER NOT NULL DEFAULT 0,
    refusal         TEXT,
    created_at      TEXT,
    updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_curve_regime_date ON curve_regime(trade_date);

-- Shares delivered by a physically-settled leg that finished ITM, and their disposal. `basis` is
-- the SETTLEMENT SPOT, not the strike — the calendars/pmcc decomposition.
CREATE TABLE IF NOT EXISTS curve_assignments (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id      TEXT NOT NULL,
    leg_role         TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    assigned_session TEXT NOT NULL,
    assigned_at      TEXT,
    direction        TEXT NOT NULL,
    shares           INTEGER NOT NULL,
    basis            REAL NOT NULL,
    strike           REAL NOT NULL,
    option_type      TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'open',
    disposed_session TEXT,
    disposed_at      TEXT,
    disposal_price   REAL,
    share_pnl        REAL,
    fees             REAL,
    created_at       TEXT,
    updated_at       TEXT,
    UNIQUE(position_id, leg_role)
);
CREATE INDEX IF NOT EXISTS idx_curve_assignments_status ON curve_assignments(status, assigned_session);
CREATE INDEX IF NOT EXISTS idx_curve_assignments_position ON curve_assignments(position_id);

-- Every management verdict, including ones an execution gate held back.
CREATE TABLE IF NOT EXISTS curve_management_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id  TEXT NOT NULL,
    occurred_at  REAL NOT NULL,
    session_date TEXT NOT NULL,
    action       TEXT NOT NULL,
    reason       TEXT NOT NULL,
    executed     INTEGER NOT NULL DEFAULT 0,
    gate         TEXT,
    detail_json  TEXT
);
CREATE INDEX IF NOT EXISTS idx_curve_events_position ON curve_management_events(position_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_curve_events_session ON curve_management_events(session_date);

-- The collapsed narrative journal (flies' shape): a gate blocking all morning is one row with a
-- count, not four hundred.
CREATE TABLE IF NOT EXISTS curve_decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT NOT NULL,
    book        TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    mode        TEXT NOT NULL,
    reason      TEXT NOT NULL,
    accepted    INTEGER NOT NULL DEFAULT 0,
    occurrences INTEGER NOT NULL DEFAULT 1,
    first_ts    TEXT,
    last_ts     TEXT,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS idx_curve_decisions_date ON curve_decisions(trade_date);

-- One UNCOLLAPSED row per evaluated entry opportunity per (symbol, book) — what was wanted, what
-- the chain offered, how far off a refusal was.
CREATE TABLE IF NOT EXISTS curve_entry_attempts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    trade_date     TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    book           TEXT NOT NULL,
    outcome        TEXT NOT NULL,
    block_detail   TEXT,
    spot           REAL,
    short_strike   REAL,
    long_strike    REAL,
    credit         REAL,
    credit_pct_of_width REAL
);
CREATE INDEX IF NOT EXISTS idx_curve_attempts_date ON curve_entry_attempts(trade_date);

-- The feed ledger: one row per (tick x symbol x kind) recording what the cache gave us, refusals
-- included — a stretch of refused rows is a feed problem, a stretch with NO rows is the loop not
-- running.
CREATE TABLE IF NOT EXISTS curve_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    trade_date    TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    kind          TEXT NOT NULL,
    status        TEXT NOT NULL,
    quotes_fresh  INTEGER,
    quotes_stale  INTEGER,
    spot          REAL
);
CREATE INDEX IF NOT EXISTS idx_curve_snapshots_date ON curve_snapshots(trade_date);

-- One row per in-session tick: the loop's own vital signs.
CREATE TABLE IF NOT EXISTS curve_loop_iterations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at         REAL NOT NULL,
    session_date   TEXT NOT NULL,
    phase          TEXT NOT NULL,
    status         TEXT NOT NULL,
    open_positions INTEGER,
    marks_written  INTEGER,
    actions_taken  INTEGER,
    note           TEXT
);
CREATE INDEX IF NOT EXISTS idx_curve_iterations_session ON curve_loop_iterations(session_date, ran_at);

-- Dates across which results must never be pooled.
CREATE TABLE IF NOT EXISTS measurement_breaks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    break_date  TEXT NOT NULL,
    key         TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    note        TEXT,
    recorded_at REAL,
    UNIQUE(break_date, key)
);
"""

# Columns added after the first release, per table. Empty at birth.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "curve_positions": {},
    "curve_legs": {},
    "curve_marks": {},
    "curve_assignments": {},
}


def default_db_path() -> str:
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    return os.path.join(home, "data", "curve", "paper_trades.db")


_store = _ledgerstore.LedgerStore("curve_", _SCHEMA, _ADDED_COLUMNS)

_now = _store.now
_upsert = _store.upsert
_migrate = _store.migrate
_declared_columns = _store.declared_columns
stale_writer_columns = _store.stale_writer_columns

save_position = _store.save_position
save_leg = _store.save_leg
save_assignment = _store.save_assignment

record_mark = _store.record_mark
record_management_event = _store.record_management_event
record_decision = _store.record_decision
record_entry_attempt = _store.record_entry_attempt
record_snapshot = _store.record_snapshot
record_iteration = _store.record_iteration
record_measurement_break = _store.record_measurement_break

open_positions = _store.open_positions
legs_for = _store.legs_for
open_legs_for = _store.open_legs_for
open_leg_expirations = _store.open_leg_expirations
open_assignments = _store.open_assignments
assignments_for = _store.assignments_for
open_assignment_count = _store.open_assignment_count


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open the ledger. WAL + NORMAL, matching every other module ledger."""
    path = db_path or os.environ.get("CURVE_DB_PATH") or default_db_path()
    conn = _core_db.connect(path, pragmas=("journal_mode=WAL", "synchronous=NORMAL"))
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


# --------------------------------------------------------------------------- readers
def open_position_for(conn, symbol: str, book: str) -> dict | None:
    r = conn.execute(
        "SELECT * FROM curve_positions WHERE symbol = ? AND book = ? AND status != 'closed' "
        "ORDER BY entry_session DESC LIMIT 1",
        (symbol, book),
    ).fetchone()
    return dict(r) if r else None


def open_position_count(conn, book: str) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM curve_positions WHERE book = ? AND status != 'closed'", (book,)
        ).fetchone()[0]
    )


def expiring_open_legs(conn, day: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT l.*, p.book, p.symbol AS position_symbol, p.status AS position_status "
            "FROM curve_legs l JOIN curve_positions p ON p.position_id = l.position_id "
            "WHERE l.status = 'open' AND l.expiration = ? ORDER BY l.position_id, l.leg_role",
            (day,),
        )
    ]


def save_regime(conn, row: dict) -> None:
    """`curve_regime` is state, not telemetry: the series' value IS its continuity, so a failed
    write here is a real problem rather than something to swallow — unlike the marks/decisions
    writers above."""
    _upsert(conn, "curve_regime", ("trade_date",), row)


def regime_for(conn, trade_date: str) -> dict | None:
    r = conn.execute("SELECT * FROM curve_regime WHERE trade_date = ?", (trade_date,)).fetchone()
    return dict(r) if r else None


def prior_ratio_before(conn, trade_date: str) -> float | None:
    """The most recent USABLE ratio strictly before `trade_date` — the hook signal's prior-day
    read. Never an unusable row's ratio (which may be None or unmeasured)."""
    r = conn.execute(
        "SELECT ratio FROM curve_regime WHERE trade_date < ? AND usable = 1 ORDER BY trade_date DESC LIMIT 1",
        (trade_date,),
    ).fetchone()
    return float(r["ratio"]) if r and r["ratio"] is not None else None
