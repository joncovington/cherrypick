"""Config loader for cherrypick-gex.

Reads a machine-local ``config.json`` (copy of ``config.example.json``); all paths in it resolve
**relative to the config file's directory**, so nothing is hardcoded. Kept deliberately tiny — this
module is a read-only viewer over another module's stream cache, so it needs a cache path, a default
symbol list, and serve defaults, nothing more.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # repo root (holds config.json / run.py)
CONFIG_PATH = ROOT / "config.json"
EXAMPLE_PATH = ROOT / "config.example.json"

_DEFAULTS = {
    "source": {"stream_cache_db": "data/stream_cache.db"},
    "symbols": ["SPX"],
    "streamer": {"window_strike_count": 20},
    "serve": {"host": "127.0.0.1", "port": 5055, "refresh_seconds": 15},
    "history_db": "data/gex_history.db",
}


def _resolve(base: Path, value: str) -> Path:
    # Expand ~ and $ENV first, so a config can point at the shared cherrypick data home
    # (e.g. "~/.cherrypick/data/meic/stream_cache.db") the same way the sibling modules do,
    # rather than only a path relative to this module. Anything still relative resolves
    # against the config file's own directory (the standalone default).
    value = os.path.expandvars(os.path.expanduser(value))
    p = Path(value)
    return p if p.is_absolute() else (base / p).resolve()


def load() -> dict:
    """Load config.json (falling back to config.example.json, then built-in defaults) and resolve
    the file paths inside it against the config file's own directory."""
    path = CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_PATH
    cfg = dict(_DEFAULTS)
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    base = path.parent if path.exists() else ROOT
    src = dict(_DEFAULTS["source"], **cfg.get("source", {}))
    cfg["source"] = src
    cfg["stream_cache_db"] = _resolve(base, src["stream_cache_db"])
    cfg["history_db_path"] = _resolve(base, cfg.get("history_db", _DEFAULTS["history_db"]))
    cfg["serve"] = dict(_DEFAULTS["serve"], **cfg.get("serve", {}))
    cfg.setdefault("symbols", _DEFAULTS["symbols"])
    return cfg


def default_symbol(cfg: dict) -> str:
    syms = cfg.get("symbols") or _DEFAULTS["symbols"]
    return str(syms[0]).upper()
