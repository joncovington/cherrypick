"""Config loader for cherrypick-gex.

Reads a machine-local ``config.json`` (copy of ``config.example.json``). Paths in it resolve
**relative to the config file's directory** unless written as ``~``/``$VAR`` (expanded) or absolute, so a
config can point at the shared cherrypick data home the way the sibling modules do. When a path key is
omitted the default lands under the suite-wide home (:mod:`cherrypick.core.home`) — ``~/.cherrypick/data/gex``
for this module's own cache/history, ``~/.cherrypick/logs/gex`` for logs — so nothing runtime is written
into the checkout. Kept deliberately tiny — this module is a read-only viewer over a stream cache.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # repo root (holds config.json / run.py)
CONFIG_PATH = ROOT / "config.json"
EXAMPLE_PATH = ROOT / "config.example.json"

# Bootstrap the cherrypick-core submodule (src/_core) so the shared home resolver imports. Idempotent.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "_core"))

from cherrypick.core import home as _home  # noqa: E402

_DEFAULTS = {
    "symbols": ["SPX"],
    "streamer": {"window_strike_count": 20},
    "serve": {"host": "127.0.0.1", "port": 5055, "refresh_seconds": 15},
}


def _resolve(base: Path, value: str) -> Path:
    # Expand ~ and $ENV first, so a config can point at the shared cherrypick data home
    # (e.g. "~/.cherrypick/data/marketdata/stream_cache.db") the same way the sibling modules do,
    # rather than only a path relative to this module. Anything still relative resolves
    # against the config file's own directory (an explicit package-local opt-in).
    value = os.path.expandvars(os.path.expanduser(value))
    p = Path(value)
    return p if p.is_absolute() else (base / p).resolve()


def _config_source() -> Path:
    """Which config to read: the home config (``~/.cherrypick/config/gex.json``) once it exists, else a
    legacy in-repo ``config.json``, else the checked-in example. Pure lookup — never writes."""
    home_cfg = _home.config_path("gex")
    if home_cfg.exists():
        return home_cfg
    if CONFIG_PATH.exists():
        return CONFIG_PATH
    return EXAMPLE_PATH


def load() -> dict:
    """Load the gex config (home config, or a legacy in-repo one until migrated; falling back to
    config.example.json, then built-in defaults). A path key that is present is resolved (``~``/``$VAR``/
    absolute honored, else relative to the config dir); a key that is omitted defaults under the
    suite-wide cherrypick home."""
    path = _config_source()
    cfg = dict(_DEFAULTS)
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    base = path.parent if path.exists() else ROOT

    src = cfg.get("source", {}) or {}
    cfg["source"] = src
    gex_data = _home.data_dir("gex")  # ~/.cherrypick/data/gex (or relocated by CHERRYPICK_HOME)
    # The stream cache defaults to the suite's CANONICAL shared cache (data/marketdata), produced by
    # whichever streamer is active — MEIC's, the standalone packages/streamer daemon, or this module's
    # own streamer when it is the producer. source.stream_cache_db overrides it. The spot-trail history_db
    # below stays gex-owned (data/gex) — that one is this module's, not shared.
    marketdata = _home.data_dir("marketdata")
    cfg["stream_cache_db"] = (
        _resolve(base, src["stream_cache_db"]) if src.get("stream_cache_db")
        else marketdata / "stream_cache.db"
    )
    cfg["history_db_path"] = (
        _resolve(base, cfg["history_db"]) if cfg.get("history_db") else gex_data / "gex_history.db"
    )
    cfg["serve"] = dict(_DEFAULTS["serve"], **cfg.get("serve", {}))
    cfg.setdefault("symbols", _DEFAULTS["symbols"])
    return cfg


def default_symbol(cfg: dict) -> str:
    syms = cfg.get("symbols") or _DEFAULTS["symbols"]
    return str(syms[0]).upper()


def ws_port(cfg: dict) -> int:
    serve = cfg.get("serve", {})
    port = int(serve.get("port", 5055))
    return int(serve.get("ws_port", port + 1))


def push_min_interval(cfg: dict) -> float:
    return float(cfg.get("serve", {}).get("push_min_interval_seconds", 1.0))


def logs_dir() -> Path:
    """This module's logs home: ``~/.cherrypick/logs/gex`` (relocated wholesale by ``CHERRYPICK_HOME``).
    A pure path — callers create it when they actually write."""
    return _home.logs_dir("gex")


def log_path(name: str) -> Path:
    """A named log file inside the logs home (see :func:`logs_dir`)."""
    return logs_dir() / name
