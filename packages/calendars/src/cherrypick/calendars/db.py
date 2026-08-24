"""The calendars paper ledger: schema, additive migrations, and every writer.

One SQLite file, `~/.cherrypick/data/calendars/paper_trades.db` — the filename is load-bearing:
`packages/review` and the advisor's fact pack resolve `<data home>/calendars/paper_trades.db`
generically, so moving or renaming it silently removes this module from both.

The schema keeps the PATH, not just the endpoints (the earnings lifecycle-tables lesson: with only
entry and exit on file, "would a tighter target have caught this?" has no answer after the fact).
`dc_marks` is the module's raison d'être — the per-tick substrate `exit_policies.py` replays every
candidate exit rule over — so a refused mark is still a row (`usable = 0` with a `refusal`): a
stalled feed and a quiet market must never look identical in the record.

Migrations are additive `ALTER TABLE ADD COLUMN` (`_migrate`), because `CREATE TABLE IF NOT EXISTS`
silently no-ops on an existing database. `stale_writer_columns` compares the running code against
the DATABASE FILE — the only comparison that catches a stale checkout writing NULLs all week (flies,
2026-08-05).

Every `record_*` writer that is pure telemetry is wrapped so a failure can never cost a tick.
"""

from __future__ import annotations

import os
import sqlite3

from cherrypick.core import db as _core_db
from cherrypick.core import ledgerstore as _ledgerstore

_SCHEMA = """
-- One row per calendar structure per book: a put-side or call-side calendar, two legs each.
-- `position_id` = "<week_of>:<book>:<side>" so a Tuesday-entry week keys identically to a Monday
-- one. `week_of` is the pairing key across books — every book's entries share the same fills, so
-- exact pairing in analysis is a JOIN, not an estimate. Entry context is stored as MEASURES
-- (spot, EM, mids, IVs), never only as buckets: a threshold can be re-cut later, a bucket cannot.
CREATE TABLE IF NOT EXISTS dc_positions (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id              TEXT NOT NULL UNIQUE,
    week_of                  TEXT NOT NULL,
    entry_session            TEXT NOT NULL,
    book                     TEXT NOT NULL,
    side                     TEXT NOT NULL,
    symbol                   TEXT NOT NULL,
    structure                TEXT NOT NULL,
    front_expiration         TEXT NOT NULL,
    back_expiration          TEXT NOT NULL,
    strike                   REAL NOT NULL,
    quantity                 INTEGER NOT NULL DEFAULT 1,
    entry_time               TEXT,
    entry_debit              REAL,
    entry_cost               REAL,
    entry_slippage           REAL,
    entry_spot               REAL,
    entry_em                 REAL,
    entry_em_pct             REAL,
    entry_front_atm_call_mid REAL,
    entry_front_atm_put_mid  REAL,
    entry_front_iv           REAL,
    entry_back_iv            REAL,
    entry_term_structure     REAL,
    entry_context            TEXT,
    advice_params            TEXT,
    status                   TEXT NOT NULL DEFAULT 'open',
    exit_reason              TEXT,
    closed_at                TEXT,
    closed_session           TEXT,
    exit_value               REAL,
    exit_cost                REAL,
    exit_slippage            REAL,
    settlement_spot          REAL,
    itm_settlements          INTEGER,
    gross_pnl                REAL,
    fees                     REAL,
    created_at               TEXT,
    updated_at               TEXT
);
CREATE INDEX IF NOT EXISTS idx_dc_positions_week ON dc_positions(week_of, book);
CREATE INDEX IF NOT EXISTS idx_dc_positions_status ON dc_positions(status);

-- Two rows per position (front short, back long). `streamer_symbol` is a flat column because the
-- market-data producer's `leg_sources` runs a plain SELECT against it every subscription poll —
-- the earnings lesson about not making the producer depend on JSON extraction. `close_kind`
-- distinguishes a traded close from cash settlement; `close_value` is the leg's per-share exit
-- value under either kind (a mark's mid, or settlement intrinsic).
CREATE TABLE IF NOT EXISTS dc_legs (
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
CREATE INDEX IF NOT EXISTS idx_dc_legs_status ON dc_legs(status, expiration);

-- THE substrate: one row per (tick x open leg), refusals included. `marked_at` is one shared
-- epoch timestamp per tick, so the derivation can reassemble a tick's four legs by equality.
-- A NULL leg_role row is a position-level refusal (the whole mark snapshot was unusable).
CREATE TABLE IF NOT EXISTS dc_marks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id  TEXT NOT NULL,
    leg_role     TEXT,
    marked_at    REAL NOT NULL,
    session_date TEXT NOT NULL,
    bid          REAL,
    ask          REAL,
    mid          REAL,
    delta        REAL,
    iv           REAL,
    vega         REAL,
    spot         REAL,
    quote_age_s  REAL,
    usable       INTEGER NOT NULL DEFAULT 0,
    refusal      TEXT
);
CREATE INDEX IF NOT EXISTS idx_dc_marks_position ON dc_marks(position_id, marked_at);
CREATE INDEX IF NOT EXISTS idx_dc_marks_session ON dc_marks(session_date);

-- Shares delivered by a PHYSICALLY-settled leg that finished ITM, and their disposal. Empty for a
-- cash-settled underlying, which is the whole reason it is its own table rather than columns on
-- `dc_legs`: an SPX week must not grow nullable share fields it can never use, and the streamer's
-- `leg_sources` SELECT over dc_legs must keep returning option symbols only.
--
-- `basis` is the SETTLEMENT SPOT, not the strike — see the decomposition in engine.py. It is what
-- makes this table purely additive to the option accounting instead of a restatement of it.
-- `assigned_session` is the expiry that delivered the shares; disposal is a later session, so the
-- gap between them IS the weekend exposure the cash model never had.
CREATE TABLE IF NOT EXISTS dc_assignments (
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
CREATE INDEX IF NOT EXISTS idx_dc_assignments_status ON dc_assignments(status, assigned_session);
CREATE INDEX IF NOT EXISTS idx_dc_assignments_position ON dc_assignments(position_id);

-- Every management verdict, including the ones an execution gate held back (executed=0 with the
-- gate) — the only record that an exit was SEEN before it was allowed (the earnings pattern).
CREATE TABLE IF NOT EXISTS dc_management_events (
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
CREATE INDEX IF NOT EXISTS idx_dc_events_position ON dc_management_events(position_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_dc_events_session ON dc_management_events(session_date);

-- The collapsed narrative journal (flies' fly_decisions shape): a gate that blocks all morning is
-- one row with a count, not four hundred.
CREATE TABLE IF NOT EXISTS dc_decisions (
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
CREATE INDEX IF NOT EXISTS idx_dc_decisions_date ON dc_decisions(trade_date);

-- One UNCOLLAPSED row per evaluated entry opportunity — the measurement record the collapsed
-- journal cannot be (flies' fly_entry_attempts lesson: retry state falls every tick, so collapsing
-- degenerates to one row per tick anyway with the machinery still in the path).
CREATE TABLE IF NOT EXISTS dc_entry_attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    trade_date    TEXT NOT NULL,
    week_of       TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    block_detail  TEXT,
    spot          REAL,
    em            REAL,
    put_target    REAL,
    call_target   REAL,
    put_strike    REAL,
    call_strike   REAL,
    put_debit     REAL,
    call_debit    REAL
);
CREATE INDEX IF NOT EXISTS idx_dc_attempts_date ON dc_entry_attempts(trade_date);

-- The feed ledger: one row per (tick x symbol) recording what the cache gave us, refusals
-- included — a stretch of refused rows is a feed problem, a stretch with NO rows is the loop not
-- running, and without this table those two silences are identical (flies' fly_snapshots lesson).
CREATE TABLE IF NOT EXISTS dc_snapshots (
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
CREATE INDEX IF NOT EXISTS idx_dc_snapshots_date ON dc_snapshots(trade_date);

-- One row per in-session tick: the loop's own vital signs, so a live-but-quiet loop is
-- distinguishable from a dead one without reading logs. Feeds the review's health reader.
CREATE TABLE IF NOT EXISTS dc_loop_iterations (
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
CREATE INDEX IF NOT EXISTS idx_dc_iterations_session ON dc_loop_iterations(session_date, ran_at);

-- Dates across which results must never be pooled (cadence changes, exit-rule changes, structure
-- redefinitions). A break is a row, not a memory — the suite review reads this table uniformly.
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

# Columns added after the first release, per table. Empty at birth — the mechanism ships with the
# schema so the first addition is an entry here, never a bare edit to _SCHEMA (which CREATE TABLE
# IF NOT EXISTS would silently ignore on an existing file).
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "dc_positions": {},
    "dc_legs": {},
    "dc_marks": {},
    # New table rather than new columns, so nothing to migrate — `CREATE TABLE IF NOT EXISTS` in
    # _SCHEMA adds it to an existing ledger on the next connect. Listed so the stale-writer guard
    # covers it too.
    "dc_assignments": {},
}


def default_db_path() -> str:
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    return os.path.join(home, "data", "calendars", "paper_trades.db")


# ---------------------------------------------------------------------------
# Row mechanics live in `cherrypick.core.ledgerstore`: 22 of these were byte-identical to pmcc's
# once the table prefix was normalized. The SCHEMA stays here -- a module owns what its tables ARE,
# the store owns how rows get into and out of them. Every public name below is the one this
# module's callers and tests already use.
_store = _ledgerstore.LedgerStore("dc_", _SCHEMA, _ADDED_COLUMNS)

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
# ---------------------------------------------------------------------------







def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open the ledger. WAL + NORMAL, because this is a write path on a 30s tick.

    It was running in SQLite's default rollback-journal mode, where every commit creates and deletes
    a journal file and fsyncs twice — and the mark path commits per leg, so a tick pays that several
    times over while the console reads the same file. WAL turns those into appends and stops readers
    and the writer blocking each other. MEIC's ledger has been WAL since it was written and the
    console reads it read-only without trouble, so this is the shape already proven in the suite
    rather than a new bet.

    NOT a measurement break: nothing about which rows get written, or their values, changes here.
    """
    path = db_path or os.environ.get("CALENDARS_DB_PATH") or default_db_path()
    conn = _core_db.connect(path, pragmas=("journal_mode=WAL", "synchronous=NORMAL"))
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


















# --------------------------------------------------------------------------- telemetry writers
# Wrapped: telemetry may never cost a trade or a tick. A decision writer failing is logged by the
# caller's own log line, never raised into the loop.














# --------------------------------------------------------------------------- readers


def pending_closing_exits(conn, expiration: str) -> list[dict]:
    """Open positions expiring on `expiration` whose book still INTENDS to close — the Friday
    regime's ordering gate (docs/friday-entry-arm.md).

    The filter is deliberately "intends to close", not "is open": `path` never closes by design, so
    a gate waiting for the book to go flat would be satisfied on no Friday ever and the Friday entry
    would silently never fire — a deadlock presenting as a skipped week, which this module has
    already produced twice for unrelated reasons and would be misdiagnosed as a third. The base-book
    split is what separates the two (see engine.base_book), so `friday:path` is excluded here for
    the same reason `path` is.
    """
    rows = conn.execute(
        "SELECT * FROM dc_positions WHERE front_expiration = ? AND status = 'open' ORDER BY book, side",
        (expiration,),
    )
    return [dict(r) for r in rows if r["book"].rsplit(":", 1)[-1] != "path"]


def positions_for_week(conn, week_of: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute("SELECT * FROM dc_positions WHERE week_of = ? ORDER BY book, side", (week_of,))
    ]








def expiring_open_legs(conn, day: str) -> list[dict]:
    """Open legs whose expiration is `day` — the settlement pass's work list, position row joined
    on so the settle path never needs a second query per leg."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT l.*, p.book, p.side AS position_side, p.symbol AS position_symbol, "
            "p.status AS position_status FROM dc_legs l JOIN dc_positions p "
            "ON p.position_id = l.position_id WHERE l.status = 'open' AND l.expiration = ? "
            "ORDER BY l.position_id, l.leg_role",
            (day,),
        )
    ]
