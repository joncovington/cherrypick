"""cherrypick.core.db — shared SQLite engine mechanics (connection + additive migrations).

Unifies the near-identical connection setup and the byte-identical additive-migration runner that
MEICAgent's and EarningsAgent's db modules duplicate (`db.py`, `db_paper.py`). This is engine
plumbing only — pure stdlib, no ORM. Table **schemas stay in the consumers**; only the mechanics
(create parent dir, `row_factory`, pragmas, "add missing columns" migration) live here.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable, Sequence
from typing import Any

# A migration is (table, column, alter_sql): run `alter_sql` only when `column` is missing from `table`.
Migration = tuple[str, str, str]


def connect(path: Any, *, row_factory: Any = sqlite3.Row, pragmas: Sequence[str] = ()) -> sqlite3.Connection:
    """Open a SQLite connection with the suite's shared conventions.

    Creates the database's parent directory, opens the connection, sets `row_factory` (defaults to
    `sqlite3.Row`; pass `None` to keep raw tuples), and applies each `PRAGMA` in `pragmas`
    (e.g. `("journal_mode=WAL", "foreign_keys=ON")`). Uses `os.path` so `":memory:"` and bare
    filenames (no parent dir) are handled without a spurious mkdir.
    """
    path = str(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    if row_factory is not None:
        conn.row_factory = row_factory
    for pragma in pragmas:
        conn.execute(f"PRAGMA {pragma}")
    return conn


def apply_additive_migrations(conn: sqlite3.Connection, migrations: Iterable[Migration]) -> list[str]:
    """Idempotently add missing columns. For each `(table, column, alter_sql)`, run `alter_sql` only
    if `column` is absent from `table` (checked via `PRAGMA table_info`). Commits once at the end.

    Returns the list of `"table.column"` actually added (for logging/tests). Safe to run on every
    startup — a no-op once every column exists. This is the exact `_migrate` both Earnings db modules
    already share, lifted to core.
    """
    added: list[str] = []
    for table, column, alter_sql in migrations:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            conn.execute(alter_sql)
            added.append(f"{table}.{column}")
    conn.commit()
    return added


def connect_ro(path: Any, *, row_factory: Any = sqlite3.Row) -> sqlite3.Connection:
    """Open a database READ-ONLY via a file: URI (mode=ro). The suite's read surfaces
    (report, calibrate, reconcile, trade_notifier, eval_activity, dashboards) all use
    this so a reader can never create, lock-for-write, or migrate a paper DB. The path
    is percent-escaped for the URI form, so directories containing '?', '#', or '%'
    cannot silently change the URI's meaning."""
    from urllib.request import pathname2url

    conn = sqlite3.connect(f"file:{pathname2url(str(path))}?mode=ro", uri=True)
    if row_factory is not None:
        conn.row_factory = row_factory
    return conn


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """`table`'s column names in declared order, or `[]` when it does not exist."""
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


# Every spelling a variant's column has had, canonical first (root CLAUDE.md, "The suite's
# vocabulary"). A ledger's column moves to `arm` in a window of its own, so a reader outside the
# module cannot know which side of that window the file it opened is on.
ARM_COLUMNS: tuple[str, ...] = ("arm", "risk_profile", "book", "profile")


def arm_column(conn: sqlite3.Connection, table: str) -> str:
    """The name `table`'s arm column goes by in THIS file: the first of `ARM_COLUMNS` present.

    For readers outside the module that owns the ledger, which have to read it on either side of
    its rename. Callers alias it at the SELECT (``f"{col} AS arm"``) and read `arm` from then on.
    Raises when there is none -- a reader that cannot find its tag column must not guess one, and
    the failure the arm migration taught (a reader asking for a column that moved, its error
    swallowed into an empty payload) is exactly what returning a default would reproduce. It raises
    `sqlite3.OperationalError`, the error the query itself would have raised on a missing column or
    table, so every reader's existing ``except sqlite3.Error`` (an uninitialised ledger, a module
    that has never run) keeps meaning what it meant.
    """
    have = columns(conn, table)
    for name in ARM_COLUMNS:
        if name in have:
            return name
    raise sqlite3.OperationalError(
        f"{table}: no arm column (looked for {', '.join(ARM_COLUMNS)}; has {have})"
    )


class PreRenameLedger(RuntimeError):
    """A ledger still carries a column under the name the code has renamed it from."""


def refuse_pre_rename(conn: sqlite3.Connection, tables: tuple[str, ...], old: str, new: str = "arm") -> None:
    """Refuse to go on against a ledger whose `old` column has not been renamed to `new` yet.

    For a module whose schema code runs on every connection (DDL plus additive migrations): on a
    ledger that still says `old`, the migration list -- which now names `new` -- would find `new`
    missing and ADD it, leaving the table with both names, history in one and every new row in the
    other, and nothing raised. Call this before the DDL and the migrations. It refuses rather than
    renaming on purpose: the rename is a reviewable step with backups
    (`scripts/arm_column_migrate.py`), not a side effect of a loop starting.
    """
    for table in tables:
        have = columns(conn, table)
        if old in have and new not in have:
            raise PreRenameLedger(
                f"{table} still has `{old}`; run `python scripts/arm_column_migrate.py --only {old} "
                "--include-backups --apply` (loops stopped, ledgers backed up) before this code touches it"
            )


def rename_column(conn: sqlite3.Connection, table: str, old: str, new: str) -> bool:
    """`ALTER TABLE <table> RENAME COLUMN <old> TO <new>`, idempotently. True when it renamed.

    **Why a rename and not add-new-keep-old.** The suite's one schema-change precedent
    (`advisor.store._migrate_enactment`) rebuilds the table, and a rebuild is the wrong tool here:
    it drops every index and constraint the original carried unless each is restored by hand, and
    every table this migration touches has at least one. `ALTER TABLE ... RENAME COLUMN` has been
    in SQLite since 3.25 (2018), carries indexes, constraints and views across untouched, and this
    repo has simply never used it.

    The tripwire also chooses for us. `stale_writer_columns` detects columns a FILE has that the
    CODE does not declare -- so an additive rename (add `arm`, keep `book`) is invisible to it for
    as long as both exist, and then false-positives forever once the old one is dropped. A rename
    leaves it honest at every point.

    Idempotent in the only way that is safe: it renames when `old` is present and `new` is not,
    returns False when the rename has already happened, and REFUSES when both columns exist at
    once. That last case is not a no-op to shrug at -- it means something wrote a half-migrated
    schema, and picking either column silently would hand back a table whose rows are split across
    two names.
    """
    have = columns(conn, table)
    if not have:
        raise ValueError(f"{table}: no such table")
    if new in have and old in have:
        raise ValueError(
            f"{table}: both {old!r} and {new!r} exist -- half-migrated,"
            " refusing to guess which holds the rows"
        )
    if new in have:
        return False
    if old not in have:
        raise ValueError(f"{table}: has neither {old!r} nor {new!r} (columns: {have})")
    conn.execute(f'ALTER TABLE {table} RENAME COLUMN "{old}" TO "{new}"')
    conn.commit()
    return True
