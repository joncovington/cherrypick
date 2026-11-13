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

Nothing runtime lands in the checkout — data, logs, and (once migrated) the config all resolve under the
home; only the checked-in ``config.example.json`` template stays in the package. Portability guardrail:
never hardcode an absolute path — the home derives from ``Path.home()`` (or the overrides).
"""

from __future__ import annotations

import os
from pathlib import Path

from cherrypick.core import home as _home
from cherrypick.core import streamcache as _streamcache


def data_dir() -> Path:
    """The resolved data home, created if it does not yet exist."""
    return _home.ensure(_home.data_dir("meic", env="MEIC_DATA_DIR"))


def data_path(name: str) -> Path:
    """A named file (or subdirectory) inside the data home."""
    return data_dir() / name


# --------------------------------------------------------------------------- the live account
# The live loop runs once per live account: the main login's (the `live` block, as always) and one per
# further broker login in the config's `live_accounts` (`live_loop --login <name>`). The process names
# its login once at startup (`live_account.activate`); every live file then follows from it. Main's
# paths and names are byte-for-byte what they always were.
MAIN_LOGIN = "main"
_live_login = MAIN_LOGIN


def set_live_login(name: str | None) -> str:
    global _live_login
    _live_login = MAIN_LOGIN if name in (None, "", MAIN_LOGIN) else str(name)
    return _live_login


def live_login() -> str:
    return _live_login


def live_key() -> str:
    """The name everything keyed by module uses for this live account: `meic` for main (the arm
    record, kill latch, published orders and open legs it always had), `meic.<login>` for another."""
    return "meic" if _live_login == MAIN_LOGIN else f"meic.{_live_login}"


def live_data_dir() -> Path:
    """Where this live account's ledger and every file kept beside it live (intents, criticals, the
    tick lock, orphans, broker health, the alert daemon's inbox): the data home for main, its own
    `<login>/` subfolder for another, so no two live accounts ever share one."""
    base = data_dir()
    return base if _live_login == MAIN_LOGIN else _home.ensure(base / _live_login)


def live_db_path() -> Path:
    """The live trade ledger (``meic_trades.db``) of this process's live account."""
    return live_data_dir() / "meic_trades.db"


def live_log_name(stem: str = "meic_live") -> str:
    """`meic_live.log` for main, `meic_live.<login>.log` for another live account."""
    return f"{stem}.log" if _live_login == MAIN_LOGIN else f"{stem}.{_live_login}.log"


def paper_db_path() -> Path:
    """The paper-trading ledger (``paper_trades.db``)."""
    return data_path("paper_trades.db")


def stream_cache_path() -> Path:
    """The DXLink stream cache (``stream_cache.db``).

    This is the suite's **canonical shared cache**, owned by infrastructure (the standalone streamer),
    not by MEIC — so it resolves under the neutral ``marketdata`` scope, not MEIC's own ``data/meic``
    dir. MEIC's streamer (when it is the active producer) writes here and every MEIC reader reads here,
    and flies/gex read the same file. It follows ``CHERRYPICK_HOME`` (the whole-home relocation) but,
    unlike MEIC's data DBs, **not** ``MEIC_DATA_DIR`` — the cache is not a MEIC-scoped artifact. See
    ``docs/streamer-package-plan.md``."""
    path = _streamcache.cache_path()
    _home.ensure(path.parent)
    return path


def config_path() -> Path:
    """MEIC's config file. The home config (``~/.cherrypick/config/meic.json``) once it exists, else the
    legacy in-repo ``config.json`` until migrated. A pure lookup — never writes — so it is safe to call
    from tests and from a standalone checkout (which keeps using its in-repo config)."""
    home_cfg = _home.config_path("meic")
    if home_cfg.exists():
        return home_cfg
    legacy = _PKG_ROOT / "config.json"
    if legacy.exists():
        return legacy
    # A fresh install has neither: the shipped example is the configuration it runs, read-only (the
    # same last resort core.home.load_module_config gives every other module). Without it the paper
    # loop opened a path that did not exist and failed every tick. Anything that WRITES the config
    # must write the home path, never this one.
    return _PKG_ROOT / "config.example.json"


def logs_dir() -> Path:
    """Where this module writes its logs: ``~/.cherrypick/logs/meic`` by default (relocated wholesale by
    ``CHERRYPICK_HOME``), or ``MEIC_LOGS_DIR`` for a machine/test override. A pure path — callers create
    it when they actually write, so importing a module never touches disk."""
    return _home.logs_dir("meic", env="MEIC_LOGS_DIR")


def log_path(name: str) -> Path:
    """A named log file inside the logs home (see :func:`logs_dir`)."""
    return logs_dir() / name


# The package root, holding the shipped config.*.example.json templates:
# src/cherrypick/meic/paths.py -> four parents up.
_PKG_ROOT = Path(__file__).resolve().parents[3]


def example_config_path() -> Path:
    """The shipped ``config.example.json`` -- the defaults a top-level value is compared against."""
    return _PKG_ROOT / "config.example.json"


def risk_profiles_path() -> Path:
    """MEIC's arm registry. ``$MEIC_RISK_CONFIG`` if set, else the machine's own
    ``~/.cherrypick/config/meic.risk.json``, else the shipped control-only
    ``config.risk.example.json``.

    The registry used to be ``config.risk.json`` in the package, which put one machine's arms in
    the repo and made every arm change a source edit. A machine's arms are its configuration, so
    they live in the home beside ``meic.json``; the package keeps only the example a fresh install
    runs from.
    """
    override = os.environ.get("MEIC_RISK_CONFIG")
    if override:
        return Path(os.path.expandvars(os.path.expanduser(override)))
    home_copy = _home.config_dir() / "meic.risk.json"
    if home_copy.is_file():
        return home_copy
    return _PKG_ROOT / "config.risk.example.json"
