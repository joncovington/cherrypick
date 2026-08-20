"""The pmcc paper ledger: schema, additive migrations, and every writer.

One SQLite file, `~/.cherrypick/data/pmcc/paper_trades.db` — the filename is load-bearing:
`packages/review` and the advisor's fact pack resolve `<data home>/pmcc/paper_trades.db`
generically, so moving or renaming it silently removes this module from both.

The schema keeps the PATH, not just the endpoints (the earnings lifecycle-tables lesson): the
per-tick `pmcc_marks` substrate carries `short_tv` and the `assignment_exposed` flag on the short
leg's rows, which is the module's honest answer to early assignment — unmodelled but MEASURED. A
refused mark is still a row (`usable = 0` with a `refusal`): a stalled feed and a quiet market must
never look identical in the record.

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
-- One row per position per book: one deep-ITM long call + one ITM short call (the current one —
-- rolls retire short_call_<n> and open short_call_<n+1>, and `short_strike`/`short_expiration`
-- track the live short). `position_id` = "<symbol>:<book>:<entry_session>". Entry context is
-- stored as MEASURES (the whole worksheet, the keltner channel reads), never only as buckets: a
-- threshold can be re-cut later, a bucket cannot. Keltner measures are stamped on EVERY book's
-- rows, not just the keltner book's, so the filter's counterfactual stays readable from control.
CREATE TABLE IF NOT EXISTS pmcc_positions (
    id                             INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id                    TEXT NOT NULL UNIQUE,
    symbol                         TEXT NOT NULL,
    book                           TEXT NOT NULL,
    entry_session                  TEXT NOT NULL,
    quantity                       INTEGER NOT NULL DEFAULT 1,
    long_expiration                TEXT NOT NULL,
    long_strike                    REAL NOT NULL,
    short_expiration               TEXT NOT NULL,
    short_strike                   REAL NOT NULL,
    entry_time                     TEXT,
    entry_spot                     REAL,
    long_entry_mid                 REAL,
    short_entry_mid                REAL,
    net_debit                      REAL,
    entry_cost                     REAL,
    entry_slippage                 REAL,
    entry_short_dte                INTEGER,
    entry_long_dte                 INTEGER,
    entry_total_premium            REAL,
    entry_short_intrinsic          REAL,
    entry_short_tv                 REAL,
    entry_net_tv                   REAL,
    entry_long_extrinsic           REAL,
    entry_profit_pct               REAL,
    entry_weekly_yield_pct         REAL,
    entry_downside_protection_pct  REAL,
    entry_breakeven                REAL,
    entry_buffer_to_breakeven_pct  REAL,
    entry_long_delta               REAL,
    entry_short_delta              REAL,
    entry_long_iv                  REAL,
    entry_short_iv                 REAL,
    long_selected_by               TEXT,
    keltner_mid                    REAL,
    keltner_atr                    REAL,
    keltner_days                   INTEGER,
    keltner_distance_atr           REAL,
    keltner_bounce_atr             REAL,
    keltner_prev_close_gap         REAL,
    advice_params                  TEXT,
    roll_count                     INTEGER NOT NULL DEFAULT 0,
    exposure_ticks                 INTEGER,
    status                         TEXT NOT NULL DEFAULT 'open',
    exit_reason                    TEXT,
    closed_at                      TEXT,
    closed_session                 TEXT,
    exit_value                     REAL,
    exit_cost                      REAL,
    exit_slippage                  REAL,
    settlement_spot                REAL,
    itm_settlements                INTEGER,
    gross_pnl                      REAL,
    fees                           REAL,
    created_at                     TEXT,
    updated_at                     TEXT
);
CREATE INDEX IF NOT EXISTS idx_pmcc_positions_session ON pmcc_positions(entry_session, book);
CREATE INDEX IF NOT EXISTS idx_pmcc_positions_status ON pmcc_positions(status);

-- Legs per position: `long_call` plus `short_call_1..n` (rolls append). `streamer_symbol` is a
-- flat column because the market-data producer's `leg_sources` runs a plain SELECT against it
-- every subscription poll — the earnings lesson about not making the producer depend on JSON
-- extraction. `close_kind` ∈ traded|rolled|expired|assigned|cash_settled; `close_value` is the
-- leg's per-share exit value under any kind (a mark's mid, or settlement intrinsic).
CREATE TABLE IF NOT EXISTS pmcc_legs (
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
CREATE INDEX IF NOT EXISTS idx_pmcc_legs_status ON pmcc_legs(status, expiration);

-- THE substrate: one row per (tick x open leg), refusals included. `marked_at` is one shared
-- epoch timestamp per tick, so a tick's legs reassemble by equality. `short_tv` and
-- `assignment_exposed` are populated on short-leg rows only — the exposure flag is the module's
-- measurement of the early-assignment region it deliberately does not model. A NULL leg_role row
-- is a position-level refusal (the whole mark snapshot was unusable).
CREATE TABLE IF NOT EXISTS pmcc_marks (
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
    vega               REAL,
    spot               REAL,
    short_tv           REAL,
    assignment_exposed INTEGER,
    quote_age_s        REAL,
    usable             INTEGER NOT NULL DEFAULT 0,
    refusal            TEXT
);
CREATE INDEX IF NOT EXISTS idx_pmcc_marks_position ON pmcc_marks(position_id, marked_at);
CREATE INDEX IF NOT EXISTS idx_pmcc_marks_session ON pmcc_marks(session_date);

-- Shares delivered by a PHYSICALLY-settled leg that finished ITM, and their disposal. `basis` is
-- the SETTLEMENT SPOT, not the strike — the calendars decomposition, which is what makes this
-- table purely additive to the option accounting instead of a restatement of it. For this module
-- the common row is SHORT shares from an assigned short call, covered the next session together
-- with the long's sale.
CREATE TABLE IF NOT EXISTS pmcc_assignments (
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
CREATE INDEX IF NOT EXISTS idx_pmcc_assignments_status ON pmcc_assignments(status, assigned_session);
CREATE INDEX IF NOT EXISTS idx_pmcc_assignments_position ON pmcc_assignments(position_id);

-- Every management verdict, including the ones an execution gate held back (executed=0 with the
-- gate) — the only record that an exit was SEEN before it was allowed (the earnings pattern).
-- Rolls land here too (`action = 'roll_short'`), detail_json carrying old/new strike, expiry, and
-- the net roll credit.
CREATE TABLE IF NOT EXISTS pmcc_management_events (
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
CREATE INDEX IF NOT EXISTS idx_pmcc_events_position ON pmcc_management_events(position_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_pmcc_events_session ON pmcc_management_events(session_date);

-- The collapsed narrative journal (flies' fly_decisions shape): a gate that blocks all morning is
-- one row with a count, not four hundred. Also the stream-window escalation's miss signal
-- (`no_deep_itm_long` / `missing_leg_quotes` occurrences).
CREATE TABLE IF NOT EXISTS pmcc_decisions (
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
CREATE INDEX IF NOT EXISTS idx_pmcc_decisions_date ON pmcc_decisions(trade_date);

-- One UNCOLLAPSED row per evaluated entry opportunity per (symbol, book) — the measurement record
-- the collapsed journal cannot be. Carries the yield search's telemetry: what was wanted, what the
-- chain offered, and how far off a refusal was.
CREATE TABLE IF NOT EXISTS pmcc_entry_attempts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    trade_date     TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    book           TEXT NOT NULL,
    outcome        TEXT NOT NULL,
    block_detail   TEXT,
    spot           REAL,
    target_yield   REAL,
    achieved_yield REAL,
    best_yield     REAL,
    long_strike    REAL,
    short_strike   REAL,
    net_debit      REAL,
    protection_pct REAL
);
CREATE INDEX IF NOT EXISTS idx_pmcc_attempts_date ON pmcc_entry_attempts(trade_date);

-- The feed ledger: one row per (tick x symbol x kind) recording what the cache gave us, refusals
-- included — a stretch of refused rows is a feed problem, a stretch with NO rows is the loop not
-- running, and without this table those two silences are identical (flies' fly_snapshots lesson).
CREATE TABLE IF NOT EXISTS pmcc_snapshots (
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
CREATE INDEX IF NOT EXISTS idx_pmcc_snapshots_date ON pmcc_snapshots(trade_date);

-- One row per in-session tick: the loop's own vital signs, so a live-but-quiet loop is
-- distinguishable from a dead one without reading logs. Feeds the review's health reader.
CREATE TABLE IF NOT EXISTS pmcc_loop_iterations (
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
CREATE INDEX IF NOT EXISTS idx_pmcc_iterations_session ON pmcc_loop_iterations(session_date, ran_at);

-- Daily OHLC bars mirrored from the shared cache's stream_summary — the keltner substrate. Kept in
-- the module's own ledger because the cache offers no retention guarantee; whatever window it holds
-- at first run seeds the history, and the module accumulates its own from there.
CREATE TABLE IF NOT EXISTS pmcc_daily_bars (
    symbol          TEXT NOT NULL,
    trade_date      TEXT NOT NULL,
    day_open        REAL,
    day_high        REAL,
    day_low         REAL,
    day_close       REAL,
    prev_day_close  REAL,
    source          TEXT,
    updated_at      REAL,
    PRIMARY KEY (symbol, trade_date)
);

-- Per-symbol stream-window escalation state (flies' fly_stream_window shape) — how wide an ATM
-- window this module is currently asking the producer for, and the miss bookkeeping behind it.
CREATE TABLE IF NOT EXISTS pmcc_stream_window (
    symbol                     TEXT PRIMARY KEY,
    width                      INTEGER,
    last_escalated_occurrences INTEGER DEFAULT 0,
    last_checked_occurrences   INTEGER DEFAULT 0,
    last_escalated_at          TEXT,
    last_miss_at               TEXT,
    updated_at                 TEXT
);

-- Dates across which results must never be pooled (cadence changes, rule changes, structure
-- redefinitions). A break is a row, not a memory — the suite review reads this table uniformly,
-- which is why it keeps the suite-wide unprefixed name.
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
    "pmcc_positions": {},
    "pmcc_legs": {},
    "pmcc_marks": {},
    "pmcc_assignments": {},
}


def default_db_path() -> str:
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    return os.path.join(home, "data", "pmcc", "paper_trades.db")


# ---------------------------------------------------------------------------
# Row mechanics live in `cherrypick.core.ledgerstore`: 22 of these were byte-identical to pmcc's
# once the table prefix was normalized. The SCHEMA stays here -- a module owns what its tables ARE,
# the store owns how rows get into and out of them. Every public name below is the one this
# module's callers and tests already use.
_store = _ledgerstore.LedgerStore("pmcc_", _SCHEMA, _ADDED_COLUMNS)

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
    """Open the ledger. WAL + NORMAL, matching the other module ledgers.

    Rollback-journal mode fsyncs twice per commit and blocks readers for the duration; this loop
    marks on a 60s tick and the console reads the same file. The module is disabled at the moment,
    so this is landed for when it runs rather than for an effect today.

    NOT a measurement break: nothing about which rows are written, or their values, changes here.
    """
    path = db_path or os.environ.get("PMCC_DB_PATH") or default_db_path()
    conn = _core_db.connect(path, pragmas=("journal_mode=WAL", "synchronous=NORMAL"))
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


















# --------------------------------------------------------------------------- telemetry writers
# Wrapped: telemetry may never cost a trade or a tick. A decision writer failing is logged by the
# caller's own log line, never raised into the loop.














# --------------------------------------------------------------------------- readers


def open_position_for(conn, symbol: str, book: str) -> dict | None:
    """The not-yet-closed position for one (symbol, book), or None — the one-position-per-symbol
    concurrency rule's lookup."""
    r = conn.execute(
        "SELECT * FROM pmcc_positions WHERE symbol = ? AND book = ? AND status != 'closed' "
        "ORDER BY entry_session DESC LIMIT 1",
        (symbol, book),
    ).fetchone()
    return dict(r) if r else None


def open_position_count(conn, book: str) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM pmcc_positions WHERE book = ? AND status != 'closed'", (book,)
        ).fetchone()[0]
    )






def next_short_role(conn, position_id: str) -> str:
    """The next `short_call_<n>` role for a roll — one past the highest already on file."""
    n = 0
    for r in conn.execute(
        "SELECT leg_role FROM pmcc_legs WHERE position_id = ? AND leg_role LIKE 'short_call_%'",
        (position_id,),
    ):
        try:
            n = max(n, int(r["leg_role"].rsplit("_", 1)[1]))
        except (ValueError, IndexError):
            continue
    return f"short_call_{n + 1}"




def expiring_open_legs(conn, day: str) -> list[dict]:
    """Open legs whose expiration is `day` — the settlement pass's work list, position row joined
    on so the settle path never needs a second query per leg."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT l.*, p.book, p.symbol AS position_symbol, p.status AS position_status "
            "FROM pmcc_legs l JOIN pmcc_positions p "
            "ON p.position_id = l.position_id WHERE l.status = 'open' AND l.expiration = ? "
            "ORDER BY l.position_id, l.leg_role",
            (day,),
        )
    ]


def rolled_today(conn, position_id: str, session_date: str) -> bool:
    """Whether this position already executed a roll this session — the once-per-day roll cadence."""
    return bool(
        conn.execute(
            "SELECT 1 FROM pmcc_management_events WHERE position_id = ? AND session_date = ? "
            "AND action = 'roll_short' AND executed = 1 LIMIT 1",
            (position_id, session_date),
        ).fetchone()
    )
