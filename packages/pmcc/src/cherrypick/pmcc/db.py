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

from cherrypick.pmcc import clock

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
    once by a newer checkout keeps columns an older checkout will silently NULL all week. Reports
    rather than repairs: a stale checkout cannot fix itself, and refusing to run would turn a
    telemetry gap into an outage.
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
    path = db_path or os.environ.get("PMCC_DB_PATH") or default_db_path()
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
    _upsert(conn, "pmcc_positions", ("position_id",), row)


def save_leg(conn, row: dict) -> None:
    _upsert(conn, "pmcc_legs", ("position_id", "leg_role"), row)


def save_assignment(conn, row: dict) -> None:
    """Not wrapped like the telemetry writers below: a delivered share position is POSITION STATE,
    not a record of one. Losing it silently would leave a position whose option legs are settled
    and whose shares nobody knows are held."""
    _upsert(conn, "pmcc_assignments", ("position_id", "leg_role"), row)


def open_assignments(conn, before_session: str | None = None) -> list[dict]:
    """Share positions still held. `before_session` restricts to those delivered on an EARLIER
    session — the disposal rule, since shares delivered by tonight's settlement cannot be covered
    until the next session opens."""
    sql = "SELECT a.*, p.book, p.quantity FROM pmcc_assignments a "
    sql += "JOIN pmcc_positions p ON p.position_id = a.position_id WHERE a.status = 'open'"
    args: list = []
    if before_session is not None:
        sql += " AND a.assigned_session < ?"
        args.append(before_session)
    return [dict(r) for r in conn.execute(sql + " ORDER BY a.assigned_session, a.position_id", args)]


def assignments_for(conn, position_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM pmcc_assignments WHERE position_id = ? ORDER BY leg_role", (position_id,)
        )
    ]


def open_assignment_count(conn, position_id: str) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM pmcc_assignments WHERE position_id = ? AND status = 'open'",
            (position_id,),
        ).fetchone()[0]
    )


# --------------------------------------------------------------------------- telemetry writers
# Wrapped: telemetry may never cost a trade or a tick. A decision writer failing is logged by the
# caller's own log line, never raised into the loop.
def record_mark(conn, **fields) -> None:
    try:
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        conn.execute(f"INSERT INTO pmcc_marks ({cols}) VALUES ({marks})", list(fields.values()))
        conn.commit()
    except Exception:  # noqa: BLE001, S110 — see the wrapper comment above
        pass


def record_management_event(conn, **fields) -> None:
    try:
        fields.setdefault("detail_json", None)
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        conn.execute(f"INSERT INTO pmcc_management_events ({cols}) VALUES ({marks})", list(fields.values()))
        conn.commit()
    except Exception:  # noqa: BLE001, S110
        pass


def record_decision(conn, *, trade_date, book, symbol, mode, reason, accepted, detail=None) -> None:
    """Collapsing journal write: a run of identical (date, book, symbol, mode, reason) rows becomes
    one row with a count."""
    try:
        ts = _now()
        row = conn.execute(
            "SELECT id, occurrences FROM pmcc_decisions WHERE trade_date = ? AND book = ? AND "
            "symbol = ? AND mode = ? AND reason = ? ORDER BY id DESC LIMIT 1",
            (trade_date, book, symbol, mode, reason),
        ).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE pmcc_decisions SET occurrences = ?, last_ts = ? WHERE id = ?",
                (row["occurrences"] + 1, ts, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO pmcc_decisions (trade_date, book, symbol, mode, reason, accepted, "
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
        conn.execute(f"INSERT INTO pmcc_entry_attempts ({cols}) VALUES ({marks})", list(fields.values()))
        conn.commit()
    except Exception:  # noqa: BLE001, S110
        pass


def record_snapshot(conn, **fields) -> None:
    try:
        fields.setdefault("ts", _now())
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        conn.execute(f"INSERT INTO pmcc_snapshots ({cols}) VALUES ({marks})", list(fields.values()))
        conn.commit()
    except Exception:  # noqa: BLE001, S110
        pass


def record_iteration(conn, **fields) -> None:
    try:
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        conn.execute(f"INSERT INTO pmcc_loop_iterations ({cols}) VALUES ({marks})", list(fields.values()))
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
            f"SELECT * FROM pmcc_positions WHERE status IN ({marks}) ORDER BY position_id", list(statuses)
        )
    ]


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


def legs_for(conn, position_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM pmcc_legs WHERE position_id = ? ORDER BY leg_role", (position_id,)
        )
    ]


def open_legs_for(conn, position_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM pmcc_legs WHERE position_id = ? AND status = 'open' ORDER BY leg_role",
            (position_id,),
        )
    ]


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


def open_leg_expirations(conn) -> list[str]:
    """Distinct expirations still held open — what the stream request must keep subscribed."""
    return [
        r["expiration"]
        for r in conn.execute(
            "SELECT DISTINCT l.expiration FROM pmcc_legs l JOIN pmcc_positions p "
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
