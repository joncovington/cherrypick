"""Data-home path resolution for cherrypick-earnings.

All runtime *data* — the live (`earnings_trades.db`) and paper (`paper_trades.db`) SQLite ledgers —
lives under a single data home, and all *logs* under a single logs home. This is a thin,
module-specific facade over :mod:`cherrypick.core.home`, the suite-wide resolver, so every writer (the
trading loop, the forced-sampling paper harness) and every reader (the strategy report/dashboard, the
orchestrator's report/reconcile/calibrate) agree on which files the module uses.

Resolution (delegated to ``cherrypick.core.home``):
  * ``EARNINGS_DATA_DIR`` / ``EARNINGS_LOGS_DIR`` env var, if set (``~`` and ``$VARS`` expanded) — a
    per-module override used by tests to point at a tmp path, and a machine escape hatch.
  * otherwise the managed cherrypick home ``~/.cherrypick/data/earnings`` and
    ``~/.cherrypick/logs/earnings`` (relocated wholesale by ``CHERRYPICK_HOME``) — shared with the
    orchestrator, whose config points at ``~/.cherrypick/data/earnings/paper_trades.db``. This is the
    same directory the locally-running ``dolt sql-server`` serves the earnings/options/stocks datasets
    from; the trade ledgers are plain SQLite files alongside those Dolt databases and never collide.

Nothing runtime lands in the checkout — data, logs, and (once migrated) the config all resolve under the
home; only the checked-in config example under ``config/`` and generated ``reports/`` stay in the
package. Portability guardrail: never hardcode an absolute path — the home derives from ``Path.home()``
(or the overrides).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the cherrypick-core submodule (src/_core) importable before the cherrypick.core import below,
# mirroring db.py/credentials.py so paths.py works even when imported first. Idempotent.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "_core"))

from cherrypick.core import home as _home  # noqa: E402


def data_dir() -> Path:
    """The resolved data home, created if it does not yet exist."""
    return _home.ensure(_home.data_dir("earnings", env="EARNINGS_DATA_DIR"))


def data_path(name: str) -> Path:
    """A named file (or subdirectory) inside the data home."""
    return data_dir() / name


def live_db_path() -> Path:
    """The live trade ledger (``earnings_trades.db``)."""
    return data_path("earnings_trades.db")


def paper_db_path() -> Path:
    """The paper-trading ledger (``paper_trades.db``)."""
    return data_path("paper_trades.db")


def config_path() -> Path:
    """Earnings' config file. The home config (``~/.cherrypick/config/earnings.json``) once it exists,
    else the legacy in-repo ``config/config.json`` until migrated. A pure lookup — never writes — so it
    is safe from tests and from a standalone checkout (which keeps using its in-repo config)."""
    home_cfg = _home.config_path("earnings")
    if home_cfg.exists():
        return home_cfg
    legacy = Path(__file__).resolve().parent.parent / "config" / "config.json"
    return legacy if legacy.exists() else home_cfg


def logs_dir() -> Path:
    """Where this module writes its logs: ``~/.cherrypick/logs/earnings`` by default (relocated wholesale
    by ``CHERRYPICK_HOME``), or ``EARNINGS_LOGS_DIR`` for a machine/test override. A pure path — callers
    create it when they actually write (mirrors the orchestrator's ``config.LOGS_DIR``)."""
    return _home.logs_dir("earnings", env="EARNINGS_LOGS_DIR")


def log_path(name: str) -> Path:
    """A named log file inside the logs home (see :func:`logs_dir`)."""
    return logs_dir() / name
