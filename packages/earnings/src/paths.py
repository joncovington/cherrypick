"""Data-home path resolution for cherrypick-earnings.

All runtime *data* — the live (`earnings_trades.db`) and paper (`paper_trades.db`) SQLite
ledgers — lives under a single data home. This is the one source of truth for that location so
every writer (the trading loop, the forced-sampling paper harness) and every reader (the
strategy report/dashboard, the orchestrator's report/reconcile/calibrate) agree on which files
the module uses.

Resolution:
  * ``EARNINGS_DATA_DIR`` env var, if set (``~`` and ``$VARS`` are expanded) — used by tests to
    point at a tmp path, and available as a machine-specific override.
  * otherwise the managed cherrypick home ``~/.cherrypick/data/earnings`` — shared with the
    orchestrator, whose config points at ``~/.cherrypick/data/earnings/paper_trades.db`` for
    cross-module reads. This is the same directory the locally-running ``dolt sql-server`` serves
    the earnings/options/stocks datasets from; the trade ledgers are plain SQLite files alongside
    those Dolt databases and never collide with them.

*Data* moves to ``.cherrypick/data/earnings`` and *logs* to ``.cherrypick/logs/earnings`` under the user
home, so nothing runtime lands in the checkout; only ``config.json`` and ``reports/`` stay in the package
directory. Portability guardrail: never hardcode an absolute path — everything derives from
``Path.home()`` (or ``CHERRYPICK_HOME``) or the ``EARNINGS_DATA_DIR`` / ``EARNINGS_LOGS_DIR`` overrides.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_HOME = Path.home() / ".cherrypick" / "data" / "earnings"


def data_dir() -> Path:
    """The resolved data home, created if it does not yet exist."""
    env = os.environ.get("EARNINGS_DATA_DIR")
    base = Path(os.path.expandvars(os.path.expanduser(env))) if env else _DEFAULT_HOME
    base.mkdir(parents=True, exist_ok=True)
    return base


def data_path(name: str) -> Path:
    """A named file (or subdirectory) inside the data home."""
    return data_dir() / name


def live_db_path() -> Path:
    """The live trade ledger (``earnings_trades.db``)."""
    return data_path("earnings_trades.db")


def paper_db_path() -> Path:
    """The paper-trading ledger (``paper_trades.db``)."""
    return data_path("paper_trades.db")


def logs_dir() -> Path:
    """Where this module writes its logs: ``~/.cherrypick/logs/earnings`` by default (the shared
    cherrypick logs home; ``CHERRYPICK_HOME`` overrides the home so it stays aligned with the
    orchestrator), or ``EARNINGS_LOGS_DIR`` for a machine/test override. A pure path — callers create it
    when they actually write (mirrors the orchestrator's ``config.LOGS_DIR``)."""
    env = os.environ.get("EARNINGS_LOGS_DIR")
    if env:
        return Path(os.path.expandvars(os.path.expanduser(env)))
    home = os.environ.get("CHERRYPICK_HOME")
    base = Path(home) if home else Path.home() / ".cherrypick"
    return base / "logs" / "earnings"


def log_path(name: str) -> Path:
    """A named log file inside the logs home (see :func:`logs_dir`)."""
    return logs_dir() / name
