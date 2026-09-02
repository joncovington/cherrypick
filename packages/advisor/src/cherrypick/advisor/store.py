"""advisor.db — the advisor's own store, and the one read-only opener for everyone else's.

Two kinds of state live in this package, and they are deliberately stored differently:

* **Mutable** state — experiments that activate, run, get tuned, expire and take a verdict — lives
  in SQLite (WAL). The cap is enforced transactionally, and the console reads SQLite natively.
* **Write-once** state — fact packs, raw replies, admitted checkpoint summaries — lives in JSON
  written atomically (tmp + replace). Those are the record of what the model was shown and said;
  nothing rewrites them.

Every read of *another package's* database goes through :func:`ro`, which opens ``?mode=ro`` via
``cherrypick.core.db``. That is the whole read posture in one function: this package cannot create,
migrate, or lock-for-write anything it does not own, and a source scan (tests/test_guardrails.py)
holds it there by proving no module in ``src/`` calls ``sqlite3.connect`` directly.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cherrypick.core import db as _db

from cherrypick.advisor import paths as _paths

SCHEMA_VERSION = 1

_SCHEMA = """
-- One row per (session, slot) checkpoint the AI actually completed. `ok` false rows are kept:
-- an outage is a fact about the day, and the console's history tab reports the ok rate.
CREATE TABLE IF NOT EXISTS checkpoints (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session           TEXT NOT NULL,
    slot              TEXT NOT NULL,
    model             TEXT,
    ok                INTEGER NOT NULL DEFAULT 1,
    error             TEXT,
    pack_path         TEXT,
    raw_path          TEXT,
    observations_json TEXT,
    flags_json        TEXT,
    created_at        TEXT NOT NULL,
    UNIQUE(session, slot)
);

-- Every proposal the model made, admitted or not, WITH the reason it was refused. A rejected
-- proposal that vanishes teaches nobody anything -- the console shows these, and they are fed back
-- to the model in the next deep pack's journal so it stops re-proposing what was already refused.
CREATE TABLE IF NOT EXISTS proposals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    checkpoint_id INTEGER,
    module        TEXT,
    kind          TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    status        TEXT NOT NULL,   -- proposed|admitted|rejected|superseded|dismissed
    reject_reason TEXT,
    experiment_id TEXT,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (checkpoint_id) REFERENCES checkpoints(id)
);
CREATE INDEX IF NOT EXISTS idx_proposals_checkpoint ON proposals(checkpoint_id);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);

-- An experiment outlives the single-session advice artifact that carries it: the artifact expires
-- every night and is re-issued from this row, re-validated against the module's CURRENT bounds.
-- `bounds_snapshot_json` records what the bounds were when it was admitted, so a later human
-- tightening that starts rejecting the overlay is visible as a change rather than a mystery.
CREATE TABLE IF NOT EXISTS experiments (
    id                     TEXT PRIMARY KEY,   -- exp-<session>-<module>-<n>
    module                 TEXT NOT NULL,
    base_profile           TEXT NOT NULL,
    name                   TEXT,
    hypothesis             TEXT,
    success_metric         TEXT,
    params_json            TEXT NOT NULL,
    bounds_snapshot_json   TEXT,
    status                 TEXT NOT NULL,      -- queued|active|expired|killed
    created_session        TEXT NOT NULL,
    expires_after_sessions INTEGER NOT NULL,
    sessions_run           INTEGER NOT NULL DEFAULT 0,
    origin_proposal_id     INTEGER,
    verdict_json           TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_experiments_module_status ON experiments(module, status);

-- The journal. Every lifecycle transition, in order, with its detail -- this is what makes an
-- experiment's history readable a month later, and it is the spine of the advisor's self-memory.
CREATE TABLE IF NOT EXISTS experiment_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL,
    session       TEXT,
    event         TEXT NOT NULL,  -- created|activated|enacted|tuned|expired|killed|verdict
    detail_json   TEXT,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (experiment_id) REFERENCES experiments(id)
);
CREATE INDEX IF NOT EXISTS idx_events_experiment ON experiment_events(experiment_id);

-- Did the artifact issued for a session reach the module's loop? Two facts that had never been
-- reconciled, and on 2026-08-25 two experiments each spent their most informative session on an
-- artifact no loop read. The reconciliation is computed in `enactment.py` and STORED here rather
-- than recomputed by each reader: the console renders the advisor's judgements and derives none of
-- its own, the same rule that keeps verdicts on the experiment row.
CREATE TABLE IF NOT EXISTS enactment (
    session         TEXT NOT NULL,
    module          TEXT NOT NULL,
    status          TEXT NOT NULL,  -- enacted|carried|not_enacted|no_artifact
    detail          TEXT,
    experiment_id   TEXT,
    artifact_params TEXT,
    decision_params TEXT,
    decision_reason TEXT,
    scored_at       TEXT NOT NULL,
    PRIMARY KEY (session, module)
);

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# Columns added after the first release; see cherrypick.core.db.apply_additive_migrations. Empty
# today -- the entry exists so the first schema change adds a line here instead of editing _SCHEMA
# (CREATE TABLE IF NOT EXISTS silently does nothing on an existing database).
_MIGRATIONS: tuple[tuple[str, str, str], ...] = ()


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the advisor's own database. WAL so the console can read it while a
    checkpoint writes."""
    conn = _db.connect(path or _paths.db_path(), pragmas=("journal_mode=WAL", "foreign_keys=ON"))
    conn.executescript(_SCHEMA)
    _db.apply_additive_migrations(conn, _MIGRATIONS)
    conn.execute(
        "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    return conn


def ro(path: Path | str) -> sqlite3.Connection:
    """**The** read-only opener — every foreign database in this package comes through here.

    Raises if the file is missing, which callers treat as "no facts from that module" rather than an
    error: a module that has never run has nothing to say about today.
    """
    return _db.connect_ro(path)


def rows(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    """Query tolerantly: a missing table or column yields no rows, not an exception.

    Fact packs read a dozen tables across five packages, several of which legitimately do not exist
    yet on a given machine (a module that has never run, a column added last week). A pack that
    fails wholesale because one optional table is absent would take the whole checkpoint down.
    """
    try:
        return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    except sqlite3.Error:
        return []


def write_json(path: Path | str, payload: Any) -> Path:
    """Atomic tmp + replace — a half-written pack must never be readable, by the script or the
    console."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
    return path


def read_json(path: Path | str, default: Any = None) -> Any:
    """Defensive read: absent or malformed both come back as `default`. Used for every foreign
    artifact (a module's advice_active.json, review's fact set) — none of which this package owns."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# --- checkpoints -------------------------------------------------------------------------------


def record_checkpoint(
    conn: sqlite3.Connection,
    *,
    session: str,
    slot: str,
    model: str | None,
    ok: bool,
    error: str | None = None,
    pack_path: str | None = None,
    raw_path: str | None = None,
    observations: list | None = None,
    flags: list | None = None,
) -> int:
    """Upsert one slot's checkpoint and return its id. Re-running a slot (`--force`) replaces the
    row rather than accumulating duplicates; the proposals from the previous attempt keep their own
    rows and their own fates."""
    conn.execute(
        "INSERT INTO checkpoints (session, slot, model, ok, error, pack_path, raw_path,"
        " observations_json, flags_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(session, slot) DO UPDATE SET model=excluded.model, ok=excluded.ok,"
        " error=excluded.error, pack_path=excluded.pack_path, raw_path=excluded.raw_path,"
        " observations_json=excluded.observations_json, flags_json=excluded.flags_json,"
        " created_at=excluded.created_at",
        (
            session,
            slot,
            model,
            1 if ok else 0,
            error,
            pack_path,
            raw_path,
            json.dumps(observations or []),
            json.dumps(flags or []),
            now_iso(),
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM checkpoints WHERE session = ? AND slot = ?", (session, slot)
    ).fetchone()
    return int(row["id"])


def add_proposal(
    conn: sqlite3.Connection,
    *,
    checkpoint_id: int | None,
    module: str | None,
    kind: str,
    payload: Any,
    status: str,
    reject_reason: str | None = None,
    experiment_id: str | None = None,
) -> int:
    conn.execute(
        "INSERT INTO proposals (checkpoint_id, module, kind, payload_json, status, reject_reason,"
        " experiment_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            checkpoint_id,
            module,
            kind,
            json.dumps(payload, default=str),
            status,
            reject_reason,
            experiment_id,
            now_iso(),
        ),
    )
    conn.commit()
    return int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])


def set_proposal_status(
    conn: sqlite3.Connection, proposal_id: int, status: str, reason: str | None = None
) -> bool:
    cur = conn.execute(
        "UPDATE proposals SET status = ?, reject_reason = COALESCE(?, reject_reason) WHERE id = ?",
        (status, reason, proposal_id),
    )
    conn.commit()
    return cur.rowcount > 0


# --- experiments -------------------------------------------------------------------------------


def next_experiment_id(conn: sqlite3.Connection, session: str, module: str) -> str:
    prefix = f"exp-{session}-{module}-"
    taken = conn.execute("SELECT COUNT(*) AS n FROM experiments WHERE id LIKE ?", (prefix + "%",)).fetchone()[
        "n"
    ]
    return f"{prefix}{int(taken) + 1}"


def insert_experiment(conn: sqlite3.Connection, record: dict[str, Any]) -> str:
    conn.execute(
        "INSERT INTO experiments (id, module, base_profile, name, hypothesis, success_metric,"
        " params_json, bounds_snapshot_json, status, created_session, expires_after_sessions,"
        " sessions_run, origin_proposal_id, verdict_json, created_at, updated_at)"
        " VALUES (:id,:module,:base_profile,:name,:hypothesis,:success_metric,:params_json,"
        " :bounds_snapshot_json,:status,:created_session,:expires_after_sessions,:sessions_run,"
        " :origin_proposal_id,:verdict_json,:created_at,:updated_at)",
        {
            "verdict_json": None,
            "sessions_run": 0,
            "name": None,
            "hypothesis": None,
            "success_metric": None,
            "bounds_snapshot_json": None,
            "origin_proposal_id": None,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            **record,
        },
    )
    conn.commit()
    return str(record["id"])


def update_experiment(conn: sqlite3.Connection, experiment_id: str, **fields: Any) -> bool:
    if not fields:
        return False
    fields["updated_at"] = now_iso()
    assignments = ", ".join(f"{k} = :{k}" for k in fields)
    cur = conn.execute(
        f"UPDATE experiments SET {assignments} WHERE id = :id", {**fields, "id": experiment_id}
    )
    conn.commit()
    return cur.rowcount > 0


def experiment(conn: sqlite3.Connection, experiment_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM experiments WHERE id = ?", (experiment_id,)).fetchone()
    return dict(row) if row else None


def experiments(
    conn: sqlite3.Connection, *, module: str | None = None, status: str | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM experiments"
    where, params = [], []
    if module:
        where.append("module = ?")
        params.append(module)
    if status:
        where.append("status = ?")
        params.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    # created_at then id: FIFO activation order for queued experiments must be deterministic.
    sql += " ORDER BY created_at, id"
    return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def journal(
    conn: sqlite3.Connection,
    experiment_id: str,
    event: str,
    *,
    session: str | None = None,
    detail: Any = None,
) -> None:
    conn.execute(
        "INSERT INTO experiment_events (experiment_id, session, event, detail_json, created_at)"
        " VALUES (?,?,?,?,?)",
        (
            experiment_id,
            session,
            event,
            json.dumps(detail, default=str) if detail is not None else None,
            now_iso(),
        ),
    )
    conn.commit()


def has_journal_event(conn: sqlite3.Connection, experiment_id: str, event: str, *, session: str) -> bool:
    """Has this experiment already been journaled with `event` for `session`?

    The idempotence check behind the enactment counter. The evening pass is re-runnable by design --
    it runs again after a failed AI call -- and a counter that advanced on every re-run would
    reintroduce, from the other direction, exactly the overcount it was written to remove.
    """
    row = conn.execute(
        "SELECT 1 FROM experiment_events WHERE experiment_id=? AND event=? AND session=? LIMIT 1",
        (experiment_id, event, session),
    ).fetchone()
    return row is not None


def events(conn: sqlite3.Connection, experiment_id: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM experiment_events WHERE experiment_id = ? ORDER BY id", (experiment_id,)
        ).fetchall()
    ]
