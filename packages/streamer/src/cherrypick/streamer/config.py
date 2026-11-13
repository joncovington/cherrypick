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
from pathlib import Path

from cherrypick.core import home as _home

# The canonical cache lives under a neutral scope owned by no trading module; the daemon's config/logs
# live under the package's own scope.
CACHE_SCOPE = "marketdata"
PKG = "streamer"

# packages/streamer -- this file sits at src/cherrypick/streamer/config.py, so the package root is
# four parents up. Only used for the in-repo config.json fallback below; everything else resolves
# through cherrypick.core.home. (Was parent.parent when modules lived flat in src/.)
_ROOT = Path(__file__).resolve().parents[3]


def _expand(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value)))


# --------------------------------------------------------------------------- instances
# `--instance NAME` runs the producer for another broker login (`streamer.instances` in the suite
# config, each named for its login). Everything an instance touches is its own, derived from the
# name: its config (`config/streamer.<name>.json`), its cache (`core.streamcache.instance_cache_path`),
# the pid and stop files beside that cache, its log, its request folder and its login's credentials.
# No flag is the main producer, byte for byte as before.
MAIN = "main"
_INSTANCE_KEY = "_instance"


def instance_of(cfg: dict) -> str:
    return str(cfg.get(_INSTANCE_KEY) or MAIN)


def is_main(cfg: dict) -> bool:
    return instance_of(cfg) == MAIN


def config_file(instance: str | None = None) -> Path:
    """Home-first (``~/.cherrypick/config/streamer.json``), else the in-repo ``config.json`` next to
    ``run.py`` (machine-local, git-ignored). An instance reads ``config/streamer.<name>.json``."""
    if instance not in (None, "", MAIN):
        return _home.config_path(f"{PKG}.{instance}")
    home_cfg = _home.config_path(PKG)
    if home_cfg.exists():
        return home_cfg
    return _ROOT / "config.json"


def load(instance: str | None = None) -> dict:
    """The producer's config. An instance with no file of its own takes main's tuning (window
    width, pacing) but none of its `symbols` and none of its `source`/`logging` paths: those would
    stream main's underlyings a second time, or write main's cache and log."""
    path = config_file(instance)
    cfg: dict = {}
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    elif instance not in (None, "", MAIN):
        base = load(None)
        cfg = {k: v for k, v in base.items() if k not in ("symbols", "source", "logging")}
    if instance not in (None, "", MAIN):
        cfg[_INSTANCE_KEY] = str(instance)
    return cfg


def symbols(cfg: dict, cli_override: list[str] | None = None) -> list[str]:
    """Traded-symbol list: CLI override > config ``symbols`` > default ``["SPX"]``. The orchestrator is
    expected to write the union of installed consumer modules' symbols into ``symbols`` at install.
    An instance has no default: it streams what its own requests declare and nothing else."""
    if cli_override:
        return [s.strip().upper() for s in cli_override if s.strip()]
    if cfg.get("symbols"):
        return [str(s).strip().upper() for s in cfg["symbols"] if str(s).strip()]
    return ["SPX"] if is_main(cfg) else []


def cache_path(cfg: dict) -> Path:
    """The canonical shared cache. ``source.stream_cache_db`` can override it, but the default — and the
    intended value for every consumer — is ``~/.cherrypick/data/marketdata/stream_cache.db``. An
    instance writes its own (``data/marketdata/<name>/stream_cache.db``), never the shared one."""
    override = (cfg.get("source") or {}).get("stream_cache_db")
    if override:
        return _expand(override)
    if not is_main(cfg):
        from cherrypick.core import streamcache as _sc

        return _sc.instance_cache_path(instance_of(cfg))
    return _home.data_dir(CACHE_SCOPE) / "stream_cache.db"


def log_path(cfg: dict) -> Path:
    override = (cfg.get("logging") or {}).get("file")
    if override:
        return _expand(override)
    name = "streamer.log" if is_main(cfg) else f"streamer.{instance_of(cfg)}.log"
    return _home.logs_dir(PKG) / name


def pid_path(cfg: dict) -> Path:
    """PID file for the single-instance guard, colocated with the cache it manages — one canonical
    producer means one PID file next to one cache."""
    return cache_path(cfg).parent / "streamer.pid"


def stop_path(cfg: dict) -> Path:
    """Stop-request file, beside the PID file for the same reason.

    A signal cannot carry a reason and, on Windows, cannot even be delivered: `os.kill(pid, SIGTERM)`
    there is `TerminateProcess`, which runs no handler and no `finally`. So `run_daemon`'s own
    "cherrypick-streamer stopped." line had never once been written in 39k lines of log, and every
    restart on this platform was recorded only by the NEXT process's startup banner.

    A file the engine polls fixes both halves: the shutting-down process logs its own exit, and the
    request can say who asked and why."""
    return cache_path(cfg).parent / "streamer.stop"
