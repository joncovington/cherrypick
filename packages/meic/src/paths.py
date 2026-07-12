"""Data-home path resolution for cherrypick-meic.

All runtime *data* — the live and paper SQLite databases, the streamer cache, and the daemon
PID/lock files — lives under a single data home. This is the one source of truth for that
location so every writer (loop, streamer, paper engine) and every reader (dashboard, the
umbrella orchestrator's report/reconcile/calibrate) agree on which files the module uses.

Resolution:
  * ``MEIC_DATA_DIR`` env var, if set (``~`` and ``$VARS`` are expanded) — used by tests to point
    at a tmp path, and available as a machine-specific override.
  * otherwise the managed cherrypick home ``~/.cherrypick/data/meic`` — shared with the umbrella,
    whose config points at ``~/.cherrypick/data/meic/paper_trades.db`` for cross-module reads.

Only *data* moves here. ``config.json`` and ``logs/`` stay in the package directory (they are part
of the checkout, not per-machine runtime state). Portability guardrail: never hardcode an absolute
path — everything derives from ``Path.home()`` or ``MEIC_DATA_DIR``.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_HOME = Path.home() / ".cherrypick" / "data" / "meic"


def data_dir() -> Path:
    """The resolved data home, created if it does not yet exist."""
    env = os.environ.get("MEIC_DATA_DIR")
    base = Path(os.path.expandvars(os.path.expanduser(env))) if env else _DEFAULT_HOME
    base.mkdir(parents=True, exist_ok=True)
    return base


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
