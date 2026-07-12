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

Both *data* and *logs* move to the user home (data under ``.cherrypick/data/meic``, logs under
``.cherrypick/logs/meic``) so nothing runtime lands in the checkout; only ``config.json`` stays in the
package directory. Portability guardrail: never hardcode an absolute path — everything derives from
``Path.home()`` (or ``CHERRYPICK_HOME``) or the ``MEIC_DATA_DIR`` / ``MEIC_LOGS_DIR`` overrides.
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


def logs_dir() -> Path:
    """Where this module writes its logs: ``~/.cherrypick/logs/meic`` by default (the shared cherrypick
    logs home; ``CHERRYPICK_HOME`` overrides the home so it stays aligned with the orchestrator), or
    ``MEIC_LOGS_DIR`` for a machine/test override. A pure path — callers create it when they actually
    write (mirrors the orchestrator's ``config.LOGS_DIR``), so importing a module never touches disk."""
    env = os.environ.get("MEIC_LOGS_DIR")
    if env:
        return Path(os.path.expandvars(os.path.expanduser(env)))
    home = os.environ.get("CHERRYPICK_HOME")
    base = Path(home) if home else Path.home() / ".cherrypick"
    return base / "logs" / "meic"


def log_path(name: str) -> Path:
    """A named log file inside the logs home (see :func:`logs_dir`)."""
    return logs_dir() / name
