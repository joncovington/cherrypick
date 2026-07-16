"""Data-home path resolution for cherrypick-meic.

All runtime *data* — the live and paper SQLite databases, the streamer cache, and the daemon
PID/lock files — lives under a single data home, and all *logs* under a single logs home. This is a
thin, module-specific facade over :mod:`cherrypick.core.home`, the suite-wide resolver, so every writer
(loop, streamer, paper engine) and every reader (dashboard, the umbrella orchestrator's
report/reconcile/calibrate) agree on which files the module uses.

Resolution (delegated to ``cherrypick.core.home``):
  * ``MEIC_DATA_DIR`` / ``MEIC_LOGS_DIR`` env var, if set (``~`` and ``$VARS`` expanded) — a per-module
    override used by tests to point at a tmp path, and available as a machine escape hatch.
  * otherwise the managed cherrypick home ``~/.cherrypick/data/meic`` and ``~/.cherrypick/logs/meic``
    (relocated wholesale by ``CHERRYPICK_HOME``) — shared with the umbrella, whose config points at
    ``~/.cherrypick/data/meic/paper_trades.db`` for cross-module reads.

Nothing runtime lands in the checkout; only ``config.json`` stays in the package directory. Portability
guardrail: never hardcode an absolute path — the home derives from ``Path.home()`` (or the overrides).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the cherrypick-core submodule (src/_core) importable before the cherrypick.core import below.
# paths.py is imported very early (before credentials.py/db.py run their own bootstrap), so it must put
# _core on sys.path itself. Idempotent — a duplicate entry is harmless.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "_core"))

from cherrypick.core import home as _home  # noqa: E402


def data_dir() -> Path:
    """The resolved data home, created if it does not yet exist."""
    return _home.ensure(_home.data_dir("meic", env="MEIC_DATA_DIR"))


def data_path(name: str) -> Path:
    """A named file (or subdirectory) inside the data home."""
    return data_dir() / name


def live_db_path() -> Path:
    """The live trade ledger (``meic_trades.db``)."""
    return data_path("meic_trades.db")


def paper_db_path() -> Path:
    """The paper-trading ledger (``paper_trades.db``)."""
    return data_path("paper_trades.db")


def stream_cache_path() -> Path:
    """The DXLink streamer cache (``stream_cache.db``)."""
    return data_path("stream_cache.db")


def logs_dir() -> Path:
    """Where this module writes its logs: ``~/.cherrypick/logs/meic`` by default (relocated wholesale by
    ``CHERRYPICK_HOME``), or ``MEIC_LOGS_DIR`` for a machine/test override. A pure path — callers create
    it when they actually write, so importing a module never touches disk."""
    return _home.logs_dir("meic", env="MEIC_LOGS_DIR")


def log_path(name: str) -> Path:
    """A named log file inside the logs home (see :func:`logs_dir`)."""
    return logs_dir() / name
