"""Config + path resolution for the standalone streamer daemon.

The producer writes the **canonical shared cache** — ``~/.cherrypick/data/marketdata/stream_cache.db``
(relocatable via ``$CHERRYPICK_HOME``) — under a neutral ``marketdata`` scope that belongs to no trading
module. That is the whole point: flies, gex, and MEIC's own readers all point ``source.stream_cache_db``
at this one path, so the cache survives whether or not MEIC is installed. The daemon's own config and
logs live under a ``streamer`` scope. See ``docs/streamer-package-plan.md``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parent / "_core"
if _CORE.is_dir() and str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from cherrypick.core import home as _home  # noqa: E402

# The canonical cache lives under a neutral scope owned by no trading module; the daemon's config/logs
# live under the package's own scope.
CACHE_SCOPE = "marketdata"
PKG = "streamer"

_ROOT = Path(__file__).resolve().parent.parent  # packages/streamer


def _expand(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value)))


def config_file() -> Path:
    """Home-first (``~/.cherrypick/config/streamer.json``), else the in-repo ``config.json`` next to
    ``run.py`` (machine-local, git-ignored)."""
    home_cfg = _home.config_path(PKG)
    if home_cfg.exists():
        return home_cfg
    return _ROOT / "config.json"


def load() -> dict:
    path = config_file()
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def symbols(cfg: dict, cli_override: list[str] | None = None) -> list[str]:
    """Traded-symbol list: CLI override > config ``symbols`` > default ``["SPX"]``. The orchestrator is
    expected to write the union of installed consumer modules' symbols into ``symbols`` at install."""
    if cli_override:
        return [s.strip().upper() for s in cli_override if s.strip()]
    if cfg.get("symbols"):
        return [str(s).strip().upper() for s in cfg["symbols"] if str(s).strip()]
    return ["SPX"]


def cache_path(cfg: dict) -> Path:
    """The canonical shared cache. ``source.stream_cache_db`` can override it, but the default — and the
    intended value for every consumer — is ``~/.cherrypick/data/marketdata/stream_cache.db``."""
    override = (cfg.get("source") or {}).get("stream_cache_db")
    if override:
        return _expand(override)
    return _home.data_dir(CACHE_SCOPE) / "stream_cache.db"


def log_path(cfg: dict) -> Path:
    override = (cfg.get("logging") or {}).get("file")
    if override:
        return _expand(override)
    return _home.logs_dir(PKG) / "streamer.log"


def pid_path(cfg: dict) -> Path:
    """PID file for the single-instance guard, colocated with the cache it manages — one canonical
    producer means one PID file next to one cache."""
    return cache_path(cfg).parent / "streamer.pid"
