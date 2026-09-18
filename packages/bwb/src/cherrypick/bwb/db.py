"""The bwb paper ledger: schema, additive migrations, and every writer.

One SQLite file, `~/.cherrypick/data/bwb/paper_trades.db` — the filename is load-bearing:
`packages/review` and the advisor's fact pack resolve `<data home>/bwb/paper_trades.db`
generically, so moving or renaming it silently removes this module from both.

`bwb_trigger_ticks` is the module's second product: keyed per (entry-session cohort x tick), NOT
per position — the telemetry (near-wing delta, peak delta, spot, gamma_flip, the below-flip latch,
measured/unmeasured) depends only on the cohort's shared strikes and is byte-identical across the
four base books, so normalizing to cohort level cuts the table 4x. The cohort key is
`(entry_session, structure_signature)` — a hash of (expiration, strikes) — not `entry_session`
alone: the four base books always share one signature, but an `advised:<base>` overlay that changes
widths or body offset holds different strikes and gets its own telemetry rows.
"""

from __future__ import annotations

import os
import sqlite3

from cherrypick.core import db as _core_db
from cherrypick.core import ledgerstore as _ledgerstore

_SCHEMA = """
-- One row per position per book: one 1-3-2 candidate. `position_id` = "<symbol>:<book>:<entry_session>".
-- Entry context is stored as MEASURES, never only as buckets. Latches persist here so a supervisor
-- restart mid-session cannot amnesia a morning trigger touch.
CREATE TABLE IF NOT EXISTS bwb_positions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id         TEXT NOT NULL UNIQUE,
    symbol              TEXT NOT NULL,
    book                TEXT NOT NULL,
    entry_session       TEXT NOT NULL,
    structure_signature TEXT NOT NULL,
    quantity            INTEGER NOT NULL DEFAULT 1,
    expiration          TEXT NOT NULL,
    body_strike         REAL NOT NULL,
    near_strike         REAL NOT NULL,
    far_strike          REAL NOT NULL,
    entry_time          TEXT,
    entry_spot          REAL,
    entry_atm_strike    REAL,
    entry_expected_move REAL,
    entry_body_mid      REAL,
    entry_near_mid      REAL,
    entry_far_mid       REAL,
    entry_credit        REAL,
    entry_narrow_width  REAL,
    entry_wide_width    REAL,
    entry_max_loss      REAL,
    entry_dte           INTEGER,
    entry_cost          REAL,
    entry_slippage      REAL,
    advice_params       TEXT,
    experiment_id       TEXT,
    -- persisted trigger latches
    peak_abs_delta      REAL,
    below_flip_seen     INTEGER NOT NULL DEFAULT 0,
    armed_at            TEXT,
    arm_reason          TEXT,
    addon_fired_at      TEXT,
    addon_short_strike  REAL,
    addon_long_strike   REAL,
    addon_credit        REAL,
    addon_cost          REAL,
    addon_slippage      REAL,
    status              TEXT NOT NULL DEFAULT 'open',
    exit_reason         TEXT,
    closed_at           TEXT,
    closed_session      TEXT,
    settlement_spot     REAL,
    itm_settlements     INTEGER,
    gross_pnl           REAL,
    fees                REAL,
    created_at          TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_bwb_positions_session ON bwb_positions(entry_session, book);
CREATE INDEX IF NOT EXISTS idx_bwb_positions_status ON bwb_positions(status);

-- Legs per position: near_long, body_short_1, body_short_2, far_long, addon_short, addon_long.
CREATE TABLE IF NOT EXISTS bwb_legs (
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
CREATE INDEX IF NOT EXISTS idx_bwb_legs_status ON bwb_legs(status, expiration);

-- Per-tick per-leg mark path, refusals included.
CREATE TABLE IF NOT EXISTS bwb_marks (
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
    spot         REAL,
    close_cost   REAL,
    quote_age_s  REAL,
    usable       INTEGER NOT NULL DEFAULT 0,
    refusal      TEXT
);
CREATE INDEX IF NOT EXISTS idx_bwb_marks_position ON bwb_marks(position_id, marked_at);
CREATE INDEX IF NOT EXISTS idx_bwb_marks_session ON bwb_marks(session_date);

-- THE second product: one row per (cohort x tick), cohort = (entry_session, structure_signature).
-- Byte-identical across the four base books that share one signature -- the shared counterfactual.
-- Carries the add-on bracket's own quotes so a replayed hypothetical fire is priceable, not just
-- timeable.
CREATE TABLE IF NOT EXISTS bwb_trigger_ticks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_session       TEXT NOT NULL,
    structure_signature TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    ticked_at           REAL NOT NULL,
    session_date        TEXT NOT NULL,
    near_abs_delta      REAL,
    peak_abs_delta      REAL,
    spot                REAL,
    gamma_flip          REAL,
    gamma_flip_basis    TEXT,
    below_flip_seen     INTEGER NOT NULL DEFAULT 0,
    addon_short_bid     REAL,
    addon_short_ask     REAL,
    addon_long_bid      REAL,
    addon_long_ask      REAL,
    measured            INTEGER NOT NULL DEFAULT 0,
    spot_measured       INTEGER NOT NULL DEFAULT 0,
    flip_measured       INTEGER NOT NULL DEFAULT 0,
    refusal             TEXT
);
CREATE INDEX IF NOT EXISTS idx_bwb_trigger_ticks_cohort
    ON bwb_trigger_ticks(entry_session, structure_signature, ticked_at);
CREATE INDEX IF NOT EXISTS idx_bwb_trigger_ticks_session ON bwb_trigger_ticks(session_date);

-- Every management verdict, including ones an execution gate held back.
CREATE TABLE IF NOT EXISTS bwb_management_events (
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
CREATE INDEX IF NOT EXISTS idx_bwb_events_position ON bwb_management_events(position_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_bwb_events_session ON bwb_management_events(session_date);

-- Collapsed narrative journal: a gate blocking all morning is one row with a count.
CREATE TABLE IF NOT EXISTS bwb_decisions (
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
CREATE INDEX IF NOT EXISTS idx_bwb_decisions_date ON bwb_decisions(trade_date);

-- One UNCOLLAPSED row per evaluated entry opportunity per (symbol, book).
CREATE TABLE IF NOT EXISTS bwb_entry_attempts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    trade_date   TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    book         TEXT NOT NULL,
    outcome      TEXT NOT NULL,
    block_detail TEXT,
    spot         REAL,
    body_strike  REAL,
    near_strike  REAL,
    far_strike   REAL,
    credit       REAL
);
CREATE INDEX IF NOT EXISTS idx_bwb_attempts_date ON bwb_entry_attempts(trade_date);

-- The feed ledger: what the cache gave us, refusals included.
CREATE TABLE IF NOT EXISTS bwb_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    trade_date   TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    kind         TEXT NOT NULL,
    status       TEXT NOT NULL,
    quotes_fresh INTEGER,
    quotes_stale INTEGER,
    spot         REAL
);
CREATE INDEX IF NOT EXISTS idx_bwb_snapshots_date ON bwb_snapshots(trade_date);

-- One row per in-session tick: the loop's own vital signs.
CREATE TABLE IF NOT EXISTS bwb_loop_iterations (
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
CREATE INDEX IF NOT EXISTS idx_bwb_iterations_session ON bwb_loop_iterations(session_date, ran_at);

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
    # The advisor experiment an advised row was entered under (2026-09-16), beside the params
    # it froze -- `advised:<base>` names a book, and every experiment on that base reuses it.
    "bwb_positions": {
        "experiment_id": "TEXT",  # The base book an advised row shadows, stamped at entry from the session decision (2026-09-17): the tag no longer carries it, and after the session the decision file is gone. Management prefers this over the configured advice.base_book, so a twin of a non-default base keeps its rules.
        "advice_base": "TEXT",
        # ---- LIVE scaffold (2026-09-18). Same schema on both ledgers; paper rows leave all of
        # these NULL. The live ledger is `live_trades.db`, a separate file (`live_db_path`).
        # Entry order: placed with a pending marker, confirmed by the broker (actual credit
        # overwrites entry_credit), or terminal (status -> 'cancelled', never established).
        "entry_order_id": "TEXT",
        "entry_external_id": "TEXT",
        "entry_fill_status": "TEXT",
        "entry_limit": "REAL",
        "entry_placed_at": "TEXT",
        "entry_live_floor": "REAL",
        "entry_reprice_count": "INTEGER",
        "entry_repriced_at": "TEXT",
        "entry_mid_at_submit": "REAL",
        # Add-on order: recorded on CONFIRMATION only (the meic rule). While pending, the plan it
        # was placed for is stashed here; a fill writes the legs through book.fire_addon with the
        # actual credit, a terminal clears the marker and the arm stays live.
        "addon_order_id": "TEXT",
        "addon_external_id": "TEXT",
        "addon_fill_status": "TEXT",
        "addon_placed_at": "TEXT",
        "addon_live_floor": "REAL",
        "addon_reprice_count": "INTEGER",
        "addon_repriced_at": "TEXT",
        "addon_mid_at_submit": "REAL",
        "addon_attempts": "INTEGER",
        "pending_addon_json": "TEXT",
        # Costs on a live row: the broker's own dry-run fee estimate when it gave one
        # (`fees_source` = 'broker_estimate'), the schedule otherwise ('modeled'), and after the
        # morning reconciliation against real transactions, 'reconciled' -- with the modeled
        # values snapshotted once so nothing is lost.
        "entry_fee_estimate": "REAL",
        "addon_fee_estimate": "REAL",
        "fees_source": "TEXT",
        "modeled_fees": "REAL",
        "modeled_gross_pnl": "REAL",
        "reconciled_at": "TEXT",
        # Which source the settlement print came from ('official' for a hand-supplied --price,
        # else the core.settlement source tag). A live row never settles on a provisional one.
        "settlement_source": "TEXT",
    },
    "bwb_legs": {},
    "bwb_marks": {},
    # `measured` ANDs two independent inputs, so a tick that had spot but no gamma flip was
    # indistinguishable from one that had neither, and the module's second product could report
    # total failure without saying which half failed. Both halves happened to be broken at once
    # (2026-08-27), and one flag could only ever have shown that as a single fact.
    "bwb_trigger_ticks": {
        "spot_measured": "INTEGER NOT NULL DEFAULT 0",
        "flip_measured": "INTEGER NOT NULL DEFAULT 0",
    },
}


def default_db_path() -> str:
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    return os.path.join(home, "data", "bwb", "paper_trades.db")


def live_db_path() -> str:
    """The LIVE ledger -- a separate file, same schema, never read by a paper surface (every
    paper reader resolves `paper_trades.db` by name). Never resolved from `BWB_DB_PATH`: that
    override points paper somewhere else, and a live loop that honoured it could be told to write
    real fills into a paper file. The orchestrator's `live_db` key points here for `report --live`."""
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    return os.path.join(home, "data", "bwb", "live_trades.db")


_store = _ledgerstore.LedgerStore("bwb_", _SCHEMA, _ADDED_COLUMNS)

_now = _store.now
_upsert = _store.upsert
_migrate = _store.migrate
_declared_columns = _store.declared_columns
stale_writer_columns = _store.stale_writer_columns

save_position = _store.save_position
save_leg = _store.save_leg

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


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open the ledger. WAL + NORMAL, matching every other module ledger."""
    path = db_path or os.environ.get("BWB_DB_PATH") or default_db_path()
    conn = _core_db.connect(path, pragmas=("journal_mode=WAL", "synchronous=NORMAL"))
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


# --------------------------------------------------------------------------- readers
def open_position_for(conn, symbol: str, book: str, entry_session: str) -> dict | None:
    r = conn.execute(
        "SELECT * FROM bwb_positions WHERE symbol = ? AND book = ? AND entry_session = ?",
        (symbol, book, entry_session),
    ).fetchone()
    return dict(r) if r else None


def open_position_count(conn, book: str) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM bwb_positions WHERE book = ? AND status != 'closed'", (book,)
        ).fetchone()[0]
    )


def expiring_open_legs(conn, day: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT l.*, p.book, p.symbol AS position_symbol, p.status AS position_status "
            "FROM bwb_legs l JOIN bwb_positions p ON p.position_id = l.position_id "
            "WHERE l.status = 'open' AND l.expiration = ? ORDER BY l.position_id, l.leg_role",
            (day,),
        )
    ]


def record_trigger_tick(conn, row: dict) -> None:
    """The second product's writer. Telemetry: swallows write failures, same discipline as marks."""
    try:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn.execute(f"INSERT INTO bwb_trigger_ticks ({cols}) VALUES ({marks})", list(row.values()))
        conn.commit()
    except Exception:  # noqa: BLE001, S110
        pass


def trigger_ticks_for_cohort(conn, entry_session: str, structure_signature: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM bwb_trigger_ticks WHERE entry_session = ? AND structure_signature = ? "
            "ORDER BY ticked_at",
            (entry_session, structure_signature),
        )
    ]


# --------------------------------------------------------------------------- live-ledger readers
def pending_entries(conn) -> list[dict]:
    """Positions whose entry order is still working at the broker."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM bwb_positions WHERE entry_fill_status = 'pending' ORDER BY entry_placed_at"
        )
    ]


def pending_addons(conn) -> list[dict]:
    """Open positions whose add-on order is still working at the broker."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM bwb_positions WHERE addon_fill_status = 'pending' AND status = 'open' "
            "ORDER BY addon_placed_at"
        )
    ]


def known_order_ids(conn) -> list[str]:
    """Every broker order id this ledger has ever recorded, entry or add-on -- the set the orphan
    sweep compares the broker's working orders against."""
    rows = conn.execute(
        "SELECT entry_order_id, addon_order_id FROM bwb_positions "
        "WHERE entry_order_id IS NOT NULL OR addon_order_id IS NOT NULL"
    ).fetchall()
    return [str(v) for r in rows for v in (r[0], r[1]) if v is not None]


def established_today(conn, book: str, day: str) -> int:
    """Structures counted against `max_structures_per_day`: today's rows for the book that were
    NOT cancelled. A cancelled entry never established anything and does not spend the budget."""
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM bwb_positions WHERE book = ? AND entry_session = ? AND status != 'cancelled'",
            (book, day),
        ).fetchone()[0]
    )


def settled_net_for_session(conn, day: str) -> float:
    """Net (gross minus fees) of every position CLOSED on `day` -- the settled-net breaker's input.
    On a hold-to-expiry ladder this only moves on a settlement day."""
    row = conn.execute(
        "SELECT COALESCE(SUM(COALESCE(gross_pnl, 0) - COALESCE(fees, 0)), 0) FROM bwb_positions "
        "WHERE closed_session = ? AND status = 'closed'",
        (day,),
    ).fetchone()
    return float(row[0] or 0.0)


def open_marked_loss(conn) -> float | None:
    """The sum, over open positions, of each one's latest usable mark-to-market loss in dollars
    (positive = losing), or None when any open position has no usable mark yet -- a breaker that
    cannot read every position must not report a number that reads as 'fine'."""
    total = 0.0
    for pos in open_positions(conn):
        # The latest tick's rows, one per leg, all carrying the position-level close_cost; any
        # unusable leg row at that tick means the position could not be priced this tick.
        r = conn.execute(
            "SELECT MIN(usable) AS usable, MAX(close_cost) AS close_cost FROM bwb_marks "
            "WHERE position_id = ? AND marked_at = "
            "(SELECT MAX(marked_at) FROM bwb_marks WHERE position_id = ?)",
            (pos["position_id"], pos["position_id"]),
        ).fetchone()
        if r is None or r["usable"] is None or not r["usable"] or r["close_cost"] is None:
            return None
        credit = float(pos.get("entry_credit") or 0.0) + float(pos.get("addon_credit") or 0.0)
        qty = int(pos.get("quantity") or 1)
        total += (float(r["close_cost"]) - credit) * 100.0 * qty
    return total
