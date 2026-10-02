"""Desk configuration — resolved defaults, the two gates, and the paths the desk owns.

The desk keeps its **own** config file (`~/.cherrypick/config/desk.json`) and reads no module's.
That separation is the point of the package: enabling the desk must never enable an automated loop,
and enabling a loop must never enable the desk. A test asserts the desk never reads a module's
`enable_live_trading`.

Every default here is the *safe* one — disabled, no accounts, no underlyings, defined risk required,
and caps small enough that a mistake costs a lesson rather than an account. A missing file refuses
everything, and so does a file that cannot be trusted: one bad cap (NaN, Infinity, a string, a
negative, an explicit null) refuses the WHOLE config, because a value that silently reads as "no cap"
is the one failure a cap exists to prevent.

The desk is off unless two things are true at once: `enabled` is literally `true` in desk.json, and
`CHERRYPICK_DESK_EXPERIMENTAL=1` is in the environment. Either alone is not enough.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home

CONFIG_NAME = "desk.json"
KEYRING_SERVICE = "cherrypick-desk"
EXPERIMENTAL_ENV = "CHERRYPICK_DESK_EXPERIMENTAL"

# Ticket lifetime: long enough for a human to read the review and answer, short enough that an
# abandoned proposal cannot be confirmed later from scrollback. Whatever is configured is clamped.
DEFAULT_TICKET_TTL_SECONDS = 120
TICKET_TTL_MIN_SECONDS = 30
TICKET_TTL_MAX_SECONDS = 300

# Numeric caps: (default, integral). Small on purpose — the desk is experimental.
CAPS: dict[str, tuple[float, bool]] = {
    "max_order_risk_dollars": (100.0, False),
    "max_orders_per_day": (2, True),
    "max_daily_risk_dollars": (200.0, False),
    "max_spreads_per_order": (1, True),
    "max_order_buying_power_dollars": (100.0, False),
    "ticket_ttl_seconds": (DEFAULT_TICKET_TTL_SECONDS, False),
}

# Marks a desk.json that exists but could not be read as a JSON object; `resolve` refuses it.
_UNREADABLE = "__unreadable__"


def config_path() -> Path:
    return _home.state_dir().parent / "config" / CONFIG_NAME


def desk_dir() -> Path:
    """Where pending tickets, the submit lock and the audit journal live (`~/.cherrypick/state/desk`)."""
    return _home.state_dir() / "desk"


def journal_path() -> Path:
    return desk_dir() / "journal.jsonl"


def load(path: Path | None = None) -> dict[str, Any]:
    """Read the desk config, or `{}` when absent. Never falls back to config.example.json — an
    example is documentation, and a desk that armed itself from one would be enabled by a copy step.
    A file that exists but is corrupt comes back marked unreadable, which `resolve` refuses."""
    p = path or config_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {_UNREADABLE: f"{p.name} could not be read as JSON: {exc}"}
    if not isinstance(data, dict):
        return {_UNREADABLE: f"{p.name} is not a JSON object"}
    return data


def _number(cfg: dict[str, Any], key: str, errors: list[str]) -> float | int:
    default, integral = CAPS[key]
    if key not in cfg:
        return default
    value = cfg[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{key} must be a number (got {value!r}); there is no 'no cap' setting")
        return default
    if not math.isfinite(value) or value < 0:
        errors.append(f"{key} must be finite and >= 0 (got {value!r})")
        return default
    if integral:
        if float(value) != int(value):
            errors.append(f"{key} must be a whole number (got {value!r})")
            return default
        return int(value)
    return float(value)


def _string_list(cfg: dict[str, Any], key: str, errors: list[str]) -> list[str]:
    value = cfg.get(key, [])
    if value is None:
        value = []
    if not isinstance(value, list) or any(
        isinstance(v, bool) or not isinstance(v, (str, int)) for v in value
    ):
        errors.append(f"{key} must be a list of strings (got {value!r})")
        return []
    return [str(v).strip() for v in value]


def _safe_defaults() -> dict[str, Any]:
    out: dict[str, Any] = {
        "enabled": False,
        "allowed_accounts": [],
        "allowed_underlyings": [],
        "require_defined_risk": True,
        "broker_keyring_service": "meicagent",
    }
    for key, (default, _) in CAPS.items():
        out[key] = default
    return out


def resolve(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Config with every default applied, plus `config_errors`. Safe-by-default: absent keys
    disable, never enable; and any invalid value refuses the whole file (every field falls back to
    its safe default and `enabled` is forced off), so a typo can never quietly widen a gate."""
    cfg = cfg if isinstance(cfg, dict) else {}
    errors: list[str] = []
    if _UNREADABLE in cfg:
        errors.append(str(cfg[_UNREADABLE]))
        cfg = {}

    caps = {key: _number(cfg, key, errors) for key in CAPS}
    caps["ticket_ttl_seconds"] = int(
        min(TICKET_TTL_MAX_SECONDS, max(TICKET_TTL_MIN_SECONDS, caps["ticket_ttl_seconds"]))
    )
    # Last-4 fragments only. Full account numbers never appear in config (suite-wide masking rule);
    # matching is done against the last 4 of the resolved account.
    accounts = [a[-4:] for a in _string_list(cfg, "allowed_accounts", errors)]
    underlyings = [u.upper() for u in _string_list(cfg, "allowed_underlyings", errors)]

    resolved: dict[str, Any] = {
        # Strict: only a literal JSON `true` arms the desk. "yes", 1 and "false" all read as off.
        "enabled": cfg.get("enabled", False) is True,
        "allowed_accounts": accounts,
        "allowed_underlyings": underlyings,
        # Strict the other way: only a literal `false` turns the requirement off.
        "require_defined_risk": cfg.get("require_defined_risk", True) is not False,
        **caps,
        # The keyring service holding the BROKER credentials to trade through. The desk has no
        # credentials of its own; it borrows an existing module's session rather than duplicating
        # secrets. It still never reads that module's trading flags.
        "broker_keyring_service": str(cfg.get("broker_keyring_service") or "meicagent"),
        "config_errors": errors,
    }
    if errors:
        return {**_safe_defaults(), "config_errors": errors}
    return resolved


def experimental_ack(environ: dict[str, str] | None = None) -> bool:
    """The second gate: `CHERRYPICK_DESK_EXPERIMENTAL=1` exported in this shell. Exactly "1"."""
    env = os.environ if environ is None else environ
    return env.get(EXPERIMENTAL_ENV) == "1"


def gate_refusals(cfg: dict[str, Any], environ: dict[str, str] | None = None) -> list[str]:
    """Why the desk is off, or [] when both gates are open and the config was accepted. Reads only
    the resolved config and the environment — no network, no keyring — so every order command can
    call it before touching either."""
    out = [f"desk.json refused: {e}" for e in cfg.get("config_errors") or []]
    if cfg.get("enabled") is not True:
        out.append("desk.enabled is not true in desk.json")
    if not experimental_ack(environ):
        out.append(f"{EXPERIMENTAL_ENV}=1 is not set in the environment")
    return out
