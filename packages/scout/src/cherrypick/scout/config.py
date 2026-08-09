"""Config loader for cherrypick-scout.

Read order: the home config (``~/.cherrypick/config/scout.json``, written once a user has one) →
a legacy in-repo ``config.json`` (for a machine that had one before this module adopted the shared
home) → the checked-in ``config.example.json`` → built-in defaults. Pure lookup — never writes.

Runtime paths (data, logs) always resolve under the suite-wide home
(:mod:`cherrypick.core.home`) — ``~/.cherrypick/data/scout`` and ``~/.cherrypick/logs/scout`` —
relocatable in one move via ``$CHERRYPICK_HOME``. Nothing here writes into the checkout.
"""

from __future__ import annotations

import json
from pathlib import Path

from cherrypick.core import home as _home

# Package root (holds config.json / config.example.json / run.py). This file sits at
# src/cherrypick/scout/config.py, so that is three parents up.
ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / "config.json"
EXAMPLE_PATH = ROOT / "config.example.json"

_DEFAULTS: dict = {
    "serve": {"host": "127.0.0.1", "port": 5057},
    "refresh": {
        "quotes_seconds": 5,
        "metrics_ttl_seconds": 900,
        "chain_ttl_seconds": 300,
        "candles_ttl_seconds": 3600,
        "candles_backfill_days": 365,
        "meta_ttl_days": 30,
        "stream_cache_max_age_seconds": 10,
    },
    "calendar": {"liquid_only": True, "use_tastytrade_earnings_watchlist": True},
    "screener": {
        "target_dte_min": 30,
        "target_dte_max": 45,
        "short_delta": 0.30,
        "wing_width_pct": 0.05,
        "min_iv_rank": 25,
        "min_liquidity_rank": 3,
    },
    "dolt": {"host": "127.0.0.1", "port": 3306, "user": "root", "connect_timeout_seconds": 5},
}


def _config_source() -> Path:
    """Which config to read: the home config once it exists, else a legacy in-repo ``config.json``,
    else the checked-in example. Pure lookup — never writes."""
    home_cfg = _home.config_path("scout")
    if home_cfg.exists():
        return home_cfg
    if CONFIG_PATH.exists():
        return CONFIG_PATH
    return EXAMPLE_PATH


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict:
    """Load the scout config (home config, else legacy in-repo, else example, else defaults),
    merged over the built-in defaults so an omitted key still resolves."""
    path = _config_source()
    cfg = dict(_DEFAULTS)
    if path.exists():
        cfg = _merge(cfg, json.loads(path.read_text(encoding="utf-8")))
    return cfg


def data_dir() -> Path:
    """This module's data home: ``~/.cherrypick/data/scout`` (relocated wholesale by
    ``CHERRYPICK_HOME``). A pure path — callers create it when they actually write."""
    return _home.data_dir("scout")


def logs_dir() -> Path:
    """This module's logs home: ``~/.cherrypick/logs/scout``. A pure path — callers create it when
    they actually write."""
    return _home.logs_dir("scout")


def cache_db_path() -> Path:
    """The module's own SQLite cache — never a cache another module owns."""
    return data_dir() / "cache.db"


def watchlist_path() -> Path:
    return data_dir() / "watchlist.json"


def log_path(name: str = "scout.log") -> Path:
    return logs_dir() / name
