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

from cherrypick.calendars import clock

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
}


def default_db_path() -> str:
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    return os.path.join(home, "data", "calendars", "paper_trades.db")


def _migrate(conn: sqlite3.Connection) -> list[str]:
    """Add any columns missing from an older DB. Returns what it added (for tests and logs)."""
    added = []
    for table, columns in _ADDED_COLUMNS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for column, sql_type in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
                added.append(f"{table}.{column}")
    if added:
        conn.commit()
    return added


def stale_writer_columns(conn: sqlite3.Connection) -> list[str]:
    """Columns the LEDGER has but this RUNNING CODE does not know. Empty is healthy.

    The flies 2026-08-05 failure shape: migration is additive and permanent, so a ledger opened
    once by a newer checkout keeps columns an older checkout will silently NULL all week. The code
    side of the comparison is the schema this file declares plus its migration table — the database
    side is the file — so the check catches exactly the stale-checkout case and nothing else.
    Reports rather than repairs: a stale checkout cannot fix itself, and refusing to run would turn
    a telemetry gap into an outage.
    """
    drift: list[str] = []
    for table, extra in _ADDED_COLUMNS.items():
        known = set(_declared_columns(table)) | set(extra)
        present = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        drift.extend(f"{table}.{c}" for c in sorted(present - known))
    return drift


def _declared_columns(table: str) -> list[str]:
    """Column names as _SCHEMA declares them, parsed from the DDL text so the two cannot drift."""
    marker = f"CREATE TABLE IF NOT EXISTS {table} ("
    start = _SCHEMA.index(marker) + len(marker)
    body = _SCHEMA[start : _SCHEMA.index(");", start)]
    cols = []
    for line in body.splitlines():
        word = line.strip().split(" ")[0]
        if word and word.isidentifier() and word.upper() not in ("UNIQUE", "PRIMARY", "FOREIGN"):
            cols.append(word)
    return cols


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or os.environ.get("CALENDARS_DB_PATH") or default_db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def _now() -> str:
    return clock.now_iso()


def _upsert(conn, table: str, keys: tuple[str, ...], row: dict) -> None:
    """Insert `row`, or update the existing row with the same natural key — a restart mid-session
    re-writes the same position rather than duplicating it."""
    row = {**row, "updated_at": _now()}
    where = " AND ".join(f"{k} = ?" for k in keys)
    existing = conn.execute(f"SELECT id FROM {table} WHERE {where}", [row[k] for k in keys]).fetchone()
    if existing is None:
        row.setdefault("created_at", _now())
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(row.values()))
    else:
        sets = ", ".join(f"{c} = ?" for c in row if c not in keys)
        vals = [v for c, v in row.items() if c not in keys] + [row[k] for k in keys]
        conn.execute(f"UPDATE {table} SET {sets} WHERE {where}", vals)
    conn.commit()


def save_position(conn, row: dict) -> None:
    _upsert(conn, "dc_positions", ("position_id",), row)


def save_leg(conn, row: dict) -> None:
    _upsert(conn, "dc_legs", ("position_id", "leg_role"), row)


# --------------------------------------------------------------------------- telemetry writers
# Wrapped: telemetry may never cost a trade or a tick. A decision writer failing is logged by the
# caller's own log line, never raised into the loop.
def record_mark(conn, **fields) -> None:
    try:
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        conn.execute(f"INSERT INTO dc_marks ({cols}) VALUES ({marks})", list(fields.values()))
        conn.commit()
    except Exception:  # noqa: BLE001, S110 — see the wrapper comment above
        pass


def record_management_event(conn, **fields) -> None:
    try:
        fields.setdefault("detail_json", None)
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        conn.execute(f"INSERT INTO dc_management_events ({cols}) VALUES ({marks})", list(fields.values()))
        conn.commit()
    except Exception:  # noqa: BLE001, S110
        pass


def record_decision(conn, *, trade_date, book, symbol, mode, reason, accepted, detail=None) -> None:
    """Collapsing journal write: a run of identical (date, book, symbol, mode, reason) rows becomes
    one row with a count."""
    try:
        ts = _now()
        row = conn.execute(
            "SELECT id, occurrences FROM dc_decisions WHERE trade_date = ? AND book = ? AND "
            "symbol = ? AND mode = ? AND reason = ? ORDER BY id DESC LIMIT 1",
            (trade_date, book, symbol, mode, reason),
        ).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE dc_decisions SET occurrences = ?, last_ts = ? WHERE id = ?",
                (row["occurrences"] + 1, ts, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO dc_decisions (trade_date, book, symbol, mode, reason, accepted, "
                "occurrences, first_ts, last_ts, detail) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (trade_date, book, symbol, mode, reason, int(bool(accepted)), ts, ts, detail),
            )
        conn.commit()
    except Exception:  # noqa: BLE001, S110
        pass


def record_entry_attempt(conn, **fields) -> None:
    try:
        fields.setdefault("ts", _now())
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        conn.execute(f"INSERT INTO dc_entry_attempts ({cols}) VALUES ({marks})", list(fields.values()))
        conn.commit()
    except Exception:  # noqa: BLE001, S110
        pass


def record_snapshot(conn, **fields) -> None:
    try:
        fields.setdefault("ts", _now())
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        conn.execute(f"INSERT INTO dc_snapshots ({cols}) VALUES ({marks})", list(fields.values()))
        conn.commit()
    except Exception:  # noqa: BLE001, S110
        pass


def record_iteration(conn, **fields) -> None:
    try:
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        conn.execute(f"INSERT INTO dc_loop_iterations ({cols}) VALUES ({marks})", list(fields.values()))
        conn.commit()
    except Exception:  # noqa: BLE001, S110
        pass


def record_measurement_break(conn, *, break_date, key, old_value=None, new_value=None, note=None) -> None:
    """NOT wrapped in the swallow-everything pattern on the insert itself — a break that fails to
    record is a real problem — but idempotent: the UNIQUE(break_date, key) makes a re-run a no-op."""
    import time as _time

    try:
        conn.execute(
            "INSERT INTO measurement_breaks (break_date, key, old_value, new_value, note, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (break_date, key, old_value, new_value, note, _time.time()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass  # already recorded — the idempotent re-run


# --------------------------------------------------------------------------- readers
def open_positions(conn, statuses: tuple[str, ...] = ("open", "short_settled")) -> list[dict]:
    marks = ", ".join("?" for _ in statuses)
    return [
        dict(r)
        for r in conn.execute(
            f"SELECT * FROM dc_positions WHERE status IN ({marks}) ORDER BY position_id", list(statuses)
        )
    ]


def positions_for_week(conn, week_of: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute("SELECT * FROM dc_positions WHERE week_of = ? ORDER BY book, side", (week_of,))
    ]


def legs_for(conn, position_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute("SELECT * FROM dc_legs WHERE position_id = ? ORDER BY leg_role", (position_id,))
    ]


def open_legs_for(conn, position_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM dc_legs WHERE position_id = ? AND status = 'open' ORDER BY leg_role",
            (position_id,),
        )
    ]


def open_leg_expirations(conn) -> list[str]:
    """Distinct expirations still held open — what the stream request must keep subscribed."""
    return [
        r["expiration"]
        for r in conn.execute(
            "SELECT DISTINCT l.expiration FROM dc_legs l JOIN dc_positions p "
            "ON p.position_id = l.position_id WHERE l.status = 'open' AND p.status != 'closed' "
            "ORDER BY l.expiration"
        )
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
