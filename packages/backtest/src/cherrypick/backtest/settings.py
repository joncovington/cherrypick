"""The resolved backtest config: `~/.cherrypick/config/backtest.json` over the declared defaults.

Strict, unlike most module configs. Every config read in the suite defaults silently, which is how a
moved key once made an A/B measure nothing while its P&L looked fine. Here a misspelt key would do
worse: a misspelt `windows` key would leave the holdout at its default without a word. So an unknown
key in any declared block is refused with its path, and so is a value of the wrong type. Keys
starting with `_` are comments and are ignored.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home

PKG_ROOT = Path(__file__).resolve().parents[3]

VENDORS = ("thetadata",)
TRANSPORTS = ("python", "terminal")
SUBSCRIPTIONS = ("free", "value", "standard", "professional")

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "vendor": {
        "name": "thetadata",
        "subscription": "standard",
        "transport": "python",
        "credential": {"keyring_service": "cherrypick-thetadata", "key": "api_key"},
        "terminal": {"host": "127.0.0.1", "port": 25503, "install_dir": None},
        "max_concurrent_requests": 4,
        "license": {"terminated_on": None, "purge_within_days": 30},
    },
    "store": {"dir": None, "cadence_seconds": 15, "strikes_each_side": 20, "request_strike_range": 50},
    "datasets": [{"symbol": "SPX", "root": "SPXW", "dte": 0, "from": "2022-05-16"}],
    "windows": {"design": ["2022-05-16", "2025-06-30"], "holdout": ["2025-07-01", "2026-10-12"]},
}

# Blocks whose keys are a closed set (the defaults above name every key).
_CLOSED = {
    (): DEFAULTS,
    ("vendor",): DEFAULTS["vendor"],
    ("vendor", "credential"): DEFAULTS["vendor"]["credential"],
    ("vendor", "terminal"): DEFAULTS["vendor"]["terminal"],
    ("vendor", "license"): DEFAULTS["vendor"]["license"],
    ("store",): DEFAULTS["store"],
    ("windows",): DEFAULTS["windows"],
}
_DATASET_KEYS = {"symbol", "root", "dte", "from"}


class SettingsError(ValueError):
    pass


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _check_keys(raw: dict, path: tuple = ()) -> None:
    allowed = _CLOSED[path]
    for k, v in raw.items():
        if k.startswith("_"):
            continue
        where = ".".join((*path, k))
        if k not in allowed:
            raise SettingsError(f"unknown key `{where}` in backtest config")
        if path + (k,) in _CLOSED:
            if not isinstance(v, dict):
                raise SettingsError(f"`{where}` must be an object")
            _check_keys(v, path + (k,))


def _check_values(s: dict) -> None:
    v = s["vendor"]
    if v["name"] not in VENDORS:
        raise SettingsError(f"vendor.name must be one of {VENDORS}, not {v['name']!r}")
    if v["transport"] not in TRANSPORTS:
        raise SettingsError(f"vendor.transport must be one of {TRANSPORTS}, not {v['transport']!r}")
    if v["subscription"] not in SUBSCRIPTIONS:
        raise SettingsError(f"vendor.subscription must be one of {SUBSCRIPTIONS}, not {v['subscription']!r}")
    if not isinstance(v["max_concurrent_requests"], int) or v["max_concurrent_requests"] < 1:
        raise SettingsError("vendor.max_concurrent_requests must be a positive integer")
    cred = v["credential"]
    if not (isinstance(cred["keyring_service"], str) and cred["keyring_service"]):
        raise SettingsError("vendor.credential.keyring_service must name a keyring service")
    if not isinstance(s["enabled"], bool):
        raise SettingsError("enabled must be true or false")
    if not isinstance(s["datasets"], list):
        raise SettingsError("datasets must be a list")
    for i, d in enumerate(s["datasets"]):
        extra = set(d) - _DATASET_KEYS - {k for k in d if k.startswith("_")}
        if extra:
            raise SettingsError(f"unknown key(s) {sorted(extra)} in datasets[{i}]")
    for name in ("design", "holdout"):
        w = s["windows"][name]
        if not (isinstance(w, list) and len(w) == 2 and all(isinstance(x, str) for x in w) and w[0] <= w[1]):
            raise SettingsError(f"windows.{name} must be [start, end] ISO dates, start first")
    design, holdout = s["windows"]["design"], s["windows"]["holdout"]
    if not design[1] < holdout[0]:
        raise SettingsError("windows.holdout must start after windows.design ends")


def resolve(raw: dict | None) -> dict:
    """Defaults overlaid with `raw`, checked. Raises `SettingsError` on any unknown key or bad value."""
    raw = raw or {}
    if not isinstance(raw, dict):
        raise SettingsError("the backtest config must be a JSON object")
    _check_keys(raw)
    settings = _merge(DEFAULTS, raw)
    _check_values(settings)
    return settings


def load(path: Path | str | None = None) -> dict:
    """The resolved config from the usual search order (`cherrypick.core.home.load_module_config`),
    ending at the shipped example, which is disabled."""
    return resolve(_home.load_module_config("backtest", PKG_ROOT, path))


def config_file_present() -> bool:
    """Whether the operator has a config of their own. The shipped example is not one: the console
    surface stays hidden on it (and it is disabled anyway)."""
    return _home.config_path("backtest").exists()
