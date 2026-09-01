"""The ledger mechanics `calendars` and `pmcc` share: upsert, migrate, telemetry writes, readers.

The two modules were built from the same design and their `db.py` files diverged only in the table
PREFIX (`dc_` / `pmcc_`). Measured before this landed: of 25 functions with matching names, **22
had byte-identical bodies once the prefix was normalized** — 203 lines per module. The three that
genuinely differ (`connect`, `default_db_path`, `expiring_open_legs`) stay in their modules.

**Schemas stay in the consumers.** This class holds no DDL: a module owns what its tables ARE, and
this owns how rows get into and out of them. `_SCHEMA` and the migration table are passed in, which
is also what keeps `stale_writer_columns` meaningful — it compares the running code's declared
columns against the database file, and both halves of that comparison belong to the module.

**The write-path contracts this preserves, which are not stylistic:**

- Telemetry writers swallow everything. A decision or mark that cannot be written must never cost a
  trade or a tick; the caller's own log line reports it.
- `save_*` writers do NOT swallow. A position or a delivered share is position STATE, not a record
  of one — losing it silently leaves a week whose legs are settled and whose shares nobody knows
  are held.
- `record_measurement_break` does not swallow its insert either (a break that fails to record is a
  real problem) but is idempotent on UNIQUE(break_date, key), so a re-run is a no-op.
- `record_decision` collapses a run of identical rows into one counted row.
"""

from __future__ import annotations

import sqlite3

from cherrypick.core.clock import now_iso

__all__ = ["LedgerStore"]


class LedgerStore:
    """Row mechanics for one prefixed ledger. Stateless apart from the prefix and the schema text."""

    def __init__(self, prefix: str, schema: str, added_columns: dict[str, dict]):
        self.prefix = prefix
        self._schema = schema
        self._added_columns = added_columns

    def table(self, name: str) -> str:
        """`positions` -> `dc_positions`. Unprefixed names (measurement_breaks) pass through."""
        return f"{self.prefix}{name}"

    # ----------------------------------------------------------------- schema / migration

    def now(self) -> str:
        return now_iso()

    def declared_columns(self, table: str) -> list[str]:
        """Column names as the schema DECLARES them, parsed from the DDL text so the two cannot
        drift. Takes a fully-qualified table name, since that is what the DDL contains."""
        marker = f"CREATE TABLE IF NOT EXISTS {table} ("
        start = self._schema.index(marker) + len(marker)
        body = self._schema[start : self._schema.index(");", start)]
        cols = []
        for line in body.splitlines():
            word = line.strip().split(" ")[0]
            if word and word.isidentifier() and word.upper() not in ("UNIQUE", "PRIMARY", "FOREIGN"):
                cols.append(word)
        return cols

    def migrate(self, conn) -> list[str]:
        """Additive-only: add declared columns a older database is missing. Never drops, never
        rewrites — an existing ledger gains columns without losing rows."""
        added: list[str] = []
        for table, columns in self._added_columns.items():
            present = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for column, decl in columns.items():
                if column not in present:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                    added.append(f"{table}.{column}")
        if added:
            conn.commit()
        return added

    def stale_writer_columns(self, conn) -> list[str]:
        """Columns the LEDGER has but this RUNNING CODE does not know. Empty is healthy.

        The flies 2026-08-05 failure shape: migration is additive and permanent, so a ledger opened
        once by a newer checkout keeps columns an older checkout will silently NULL all week. The
        code side of the comparison is the declared schema plus the migration table, the database
        side is the file, so this catches exactly the stale-checkout case and nothing else. Reports
        rather than repairs: a stale checkout cannot fix itself, and refusing to run would turn a
        telemetry gap into an outage.
        """
        drift: list[str] = []
        for table, extra in self._added_columns.items():
            known = set(self.declared_columns(table)) | set(extra)
            present = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            drift.extend(f"{table}.{c}" for c in sorted(present - known))
        return drift

    # ----------------------------------------------------------------- state writers (never swallow)

    def upsert(self, conn, table: str, keys: tuple[str, ...], row: dict) -> None:
        """Insert `row`, or update the existing row with the same natural key — a restart mid-session
        re-writes the same position rather than duplicating it."""
        row = {**row, "updated_at": self.now()}
        where = " AND ".join(f"{k} = ?" for k in keys)
        existing = conn.execute(
            f"SELECT id FROM {table} WHERE {where}", [row[k] for k in keys]
        ).fetchone()
        if existing is None:
            row.setdefault("created_at", self.now())
            cols = ", ".join(row)
            marks = ", ".join("?" for _ in row)
            conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(row.values()))
        else:
            sets = ", ".join(f"{c} = ?" for c in row if c not in keys)
            vals = [v for c, v in row.items() if c not in keys] + [row[k] for k in keys]
            conn.execute(f"UPDATE {table} SET {sets} WHERE {where}", vals)
        conn.commit()

    def save_position(self, conn, row: dict) -> None:
        self.upsert(conn, self.table("positions"), ("position_id",), row)

    def save_leg(self, conn, row: dict) -> None:
        self.upsert(conn, self.table("legs"), ("position_id", "leg_role"), row)

    def save_assignment(self, conn, row: dict) -> None:
        """Not wrapped like the telemetry writers: a delivered share position is POSITION STATE, not
        a record of one. Losing it silently would leave a week whose option legs are settled and
        whose shares nobody knows are held."""
        self.upsert(conn, self.table("assignments"), ("position_id", "leg_role"), row)

    # ----------------------------------------------------------------- telemetry writers (swallow)
    # Telemetry may never cost a trade or a tick. A writer failing is reported by the caller's own
    # log line, never raised into the loop.

    def _insert(self, conn, table: str, fields: dict) -> None:
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(fields.values()))
        conn.commit()

    def record_mark(self, conn, **fields) -> None:
        try:
            self._insert(conn, self.table("marks"), fields)
        except Exception:  # noqa: BLE001, S110 — see the section comment above
            pass

    def record_management_event(self, conn, **fields) -> None:
        try:
            fields.setdefault("detail_json", None)
            self._insert(conn, self.table("management_events"), fields)
        except Exception:  # noqa: BLE001, S110
            pass

    def record_entry_attempt(self, conn, **fields) -> None:
        try:
            fields.setdefault("ts", self.now())
            self._insert(conn, self.table("entry_attempts"), fields)
        except Exception:  # noqa: BLE001, S110
            pass

    def record_snapshot(self, conn, **fields) -> None:
        try:
            fields.setdefault("ts", self.now())
            self._insert(conn, self.table("snapshots"), fields)
        except Exception:  # noqa: BLE001, S110
            pass

    def record_iteration(self, conn, **fields) -> None:
        try:
            self._insert(conn, self.table("loop_iterations"), fields)
        except Exception:  # noqa: BLE001, S110
            pass

    def record_decision(
        self, conn, *, trade_date, book, symbol, mode, reason, accepted, detail=None
    ) -> None:
        """Collapsing journal write: a run of identical (date, book, symbol, mode, reason) rows
        becomes one row with a count."""
        table = self.table("decisions")
        try:
            ts = self.now()
            row = conn.execute(
                f"SELECT id, occurrences FROM {table} WHERE trade_date = ? AND book = ? AND "
                "symbol = ? AND mode = ? AND reason = ? ORDER BY id DESC LIMIT 1",
                (trade_date, book, symbol, mode, reason),
            ).fetchone()
            if row is not None:
                conn.execute(
                    f"UPDATE {table} SET occurrences = ?, last_ts = ? WHERE id = ?",
                    (row["occurrences"] + 1, ts, row["id"]),
                )
            else:
                conn.execute(
                    f"INSERT INTO {table} (trade_date, book, symbol, mode, reason, accepted, "
                    "occurrences, first_ts, last_ts, detail) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                    (trade_date, book, symbol, mode, reason, int(bool(accepted)), ts, ts, detail),
                )
            conn.commit()
        except Exception:  # noqa: BLE001, S110
            pass

    def record_measurement_break(
        self, conn, *, break_date, key, old_value=None, new_value=None, note=None
    ) -> None:
        """NOT wrapped in the swallow-everything pattern on the insert itself — a break that fails to
        record is a real problem — but idempotent: the UNIQUE(break_date, key) makes a re-run a
        no-op. The table is unprefixed; both modules spell it `measurement_breaks`."""
        import time as _time

        try:
            conn.execute(
                "INSERT INTO measurement_breaks "
                "(break_date, key, old_value, new_value, note, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                (break_date, key, old_value, new_value, note, _time.time()),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            pass  # already recorded — the idempotent re-run

    # ----------------------------------------------------------------- readers

    def open_positions(
        self, conn, statuses: tuple[str, ...] = ("open", "short_settled")
    ) -> list[dict]:
        marks = ", ".join("?" for _ in statuses)
        return [
            dict(r)
            for r in conn.execute(
                f"SELECT * FROM {self.table('positions')} WHERE status IN ({marks}) "
                "ORDER BY position_id",
                list(statuses),
            )
        ]

    def legs_for(self, conn, position_id: str) -> list[dict]:
        return [
            dict(r)
            for r in conn.execute(
                f"SELECT * FROM {self.table('legs')} WHERE position_id = ? ORDER BY leg_role",
                (position_id,),
            )
        ]

    def open_legs_for(self, conn, position_id: str) -> list[dict]:
        return [
            dict(r)
            for r in conn.execute(
                f"SELECT * FROM {self.table('legs')} WHERE position_id = ? AND status = 'open' "
                "ORDER BY leg_role",
                (position_id,),
            )
        ]

    def open_leg_expirations(self, conn) -> list[str]:
        """Distinct expirations still held open — what the stream request must keep subscribed."""
        return [
            r["expiration"]
            for r in conn.execute(
                f"SELECT DISTINCT l.expiration FROM {self.table('legs')} l "
                f"JOIN {self.table('positions')} p ON p.position_id = l.position_id "
                "WHERE l.status = 'open' AND p.status != 'closed' ORDER BY l.expiration"
            )
        ]

    def open_assignments(self, conn, before_session: str | None = None) -> list[dict]:
        """Share positions still held. `before_session` restricts to those delivered on an EARLIER
        session — the disposal rule, since shares delivered by tonight's settlement cannot be sold
        until the next session opens."""
        sql = (
            f"SELECT a.*, p.book, p.quantity FROM {self.table('assignments')} a "
            f"JOIN {self.table('positions')} p ON p.position_id = a.position_id "
            "WHERE a.status = 'open'"
        )
        args: list = []
        if before_session is not None:
            sql += " AND a.assigned_session < ?"
            args.append(before_session)
        return [
            dict(r)
            for r in conn.execute(sql + " ORDER BY a.assigned_session, a.position_id", args)
        ]

    def assignments_for(self, conn, position_id: str) -> list[dict]:
        return [
            dict(r)
            for r in conn.execute(
                f"SELECT * FROM {self.table('assignments')} WHERE position_id = ? ORDER BY leg_role",
                (position_id,),
            )
        ]

    def open_assignment_count(self, conn, position_id: str) -> int:
        return int(
            conn.execute(
                f"SELECT COUNT(*) FROM {self.table('assignments')} "
                "WHERE position_id = ? AND status = 'open'",
                (position_id,),
            ).fetchone()[0]
        )
