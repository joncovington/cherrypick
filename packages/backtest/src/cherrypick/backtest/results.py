"""The results store (`results/backtest.db`): runs, the trial ledger, per-session results and spent
holdouts. Derived from licensed data, so it stays under the home like everything else here.

**The trial ledger is complete by construction.** Every run goes through `runner.run`, which writes a
trial row for every (spec, arm) it evaluates on the design window. A spec is identified by a hash of
what decides its trades (the arm's resolved parameters, the cadence and the fill model), not by its
name or the window, so re-running the same spec adds nothing and changing one parameter adds one.
DSR deflates by this count; it cannot be argued down after the fact because nothing else writes it.

**A holdout is spent once per spec**, recorded in `holdout_spent`, and refused after. `control` is
never recorded there: it is paired with, never judged.
"""

from __future__ import annotations

import json
import sqlite3
import time

from . import paths

DDL = (
    "CREATE TABLE IF NOT EXISTS runs ("
    "run_id TEXT PRIMARY KEY, created_at REAL NOT NULL, module TEXT NOT NULL, arms TEXT NOT NULL, "
    "window_name TEXT NOT NULL, start TEXT, end TEXT, cadence INTEGER, code_commit TEXT, "
    "data_fingerprint TEXT, fill_model TEXT, status TEXT NOT NULL, sessions INTEGER, refused TEXT, "
    "report TEXT)",
    "CREATE TABLE IF NOT EXISTS trials ("
    "module TEXT NOT NULL, spec_hash TEXT NOT NULL, arm TEXT NOT NULL, spec TEXT NOT NULL, "
    "first_run TEXT NOT NULL, sessions INTEGER, sharpe REAL, created_at REAL NOT NULL, "
    "PRIMARY KEY (module, spec_hash))",
    "CREATE TABLE IF NOT EXISTS session_results ("
    "run_id TEXT NOT NULL, arm TEXT NOT NULL, spec_hash TEXT NOT NULL, session TEXT NOT NULL, "
    "net REAL, gross REAL, fees REAL, trades INTEGER, PRIMARY KEY (run_id, arm, session))",
    "CREATE TABLE IF NOT EXISTS holdout_spent ("
    "module TEXT NOT NULL, spec_hash TEXT NOT NULL, arm TEXT NOT NULL, run_id TEXT NOT NULL, "
    "spent_at REAL NOT NULL, PRIMARY KEY (module, spec_hash))",
)


def connect() -> sqlite3.Connection:
    paths.results_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(paths.results_db())
    conn.row_factory = sqlite3.Row
    for stmt in DDL:
        conn.execute(stmt)
    conn.commit()
    return conn


def record_trial(
    conn, *, module: str, spec_hash: str, arm: str, spec: dict, run_id: str, sessions: int, sharpe
):
    """One trial per distinct spec. A re-run of the same spec keeps the first row (it is the same
    trial) but its latest Sharpe, so the cross-trial variance reflects current data."""
    conn.execute(
        "INSERT INTO trials (module, spec_hash, arm, spec, first_run, sessions, sharpe, created_at) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(module, spec_hash) DO UPDATE SET sessions = excluded.sessions, "
        "sharpe = excluded.sharpe",
        (module, spec_hash, arm, json.dumps(spec, sort_keys=True), run_id, sessions, sharpe, time.time()),
    )


def trial_count(conn, module: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM trials WHERE module = ?", (module,)).fetchone()[0]


def trial_sharpes(conn, module: str) -> list[float]:
    return [
        r[0]
        for r in conn.execute("SELECT sharpe FROM trials WHERE module = ? AND sharpe IS NOT NULL", (module,))
    ]


def holdout_spent(conn, module: str, spec_hash: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM holdout_spent WHERE module = ? AND spec_hash = ?", (module, spec_hash)
        ).fetchone()
        is not None
    )


def spend_holdout(conn, *, module: str, spec_hash: str, arm: str, run_id: str) -> None:
    conn.execute(
        "INSERT INTO holdout_spent (module, spec_hash, arm, run_id, spent_at) VALUES (?,?,?,?,?)",
        (module, spec_hash, arm, run_id, time.time()),
    )
