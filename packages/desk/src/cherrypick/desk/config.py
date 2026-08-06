"""Desk configuration — resolved defaults, and the paths the desk owns.

The desk keeps its **own** config file (`~/.cherrypick/config/desk.json`) and reads no module's.
That separation is the point of the package: enabling the desk must never enable an automated loop,
and enabling a loop must never enable the desk. A test asserts the desk never reads a module's
`enable_live_trading`.

Every default here is the *safe* one — disabled, no accounts allowed, defined-risk required. A
missing or empty config file therefore refuses everything rather than falling open.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home

CONFIG_NAME = "desk.json"
KEYRING_SERVICE = "cherrypick-desk"

# Ticket lifetime. Long enough for a human to read the review and answer, short enough that an
# abandoned proposal cannot be confirmed later from scrollback.
DEFAULT_TICKET_TTL_SECONDS = 180
DEFAULT_MAX_ORDER_RISK = 500.0


def config_path() -> Path:
    return _home.state_dir().parent / "config" / CONFIG_NAME


def desk_dir() -> Path:
    """Where pending tickets and the audit journal live (`~/.cherrypick/state/desk`)."""
    return _home.state_dir() / "desk"


def journal_path() -> Path:
    return desk_dir() / "journal.jsonl"


def load(path: Path | None = None) -> dict[str, Any]:
    """Read the desk config, or `{}` when absent. Never raises on a missing file — `resolve()`
    turns an empty dict into a fully-disabled configuration, which is the correct fallback."""
    p = path or config_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A corrupt config must not read as permissive. Returning {} lands on the disabled defaults.
        return {}


def resolve(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Config with every default applied. Safe-by-default: absent keys disable, never enable."""
    cfg = cfg or {}
    return {
        "enabled": bool(cfg.get("enabled", False)),
        # Last-4 fragments only. Full account numbers never appear in config (suite-wide masking rule);
        # matching is done against the last 4 of the resolved account at submit time.
        "allowed_accounts": [str(a).strip()[-4:] for a in (cfg.get("allowed_accounts") or [])],
        "require_defined_risk": bool(cfg.get("require_defined_risk", True)),
        # An explicit null means "no per-order cap" — a deliberate choice, distinct from an ABSENT
        # key, which lands on the $500 default. Forgetting the key must never disable the cap.
        "max_order_risk_dollars": (
            None
            if "max_order_risk_dollars" in cfg and cfg["max_order_risk_dollars"] is None
            else float(cfg.get("max_order_risk_dollars", DEFAULT_MAX_ORDER_RISK))
        ),
        "ticket_ttl_seconds": int(cfg.get("ticket_ttl_seconds", DEFAULT_TICKET_TTL_SECONDS)),
        # Optional daily brakes — off unless set, so they never surprise. Counted from the journal.
        "max_orders_per_day": cfg.get("max_orders_per_day"),
        "max_daily_risk_dollars": cfg.get("max_daily_risk_dollars"),
        # The keyring service holding the BROKER credentials to trade through. The desk has no
        # credentials of its own; it borrows an existing module's session rather than duplicating
        # secrets. It still never reads that module's trading flags.
        "broker_keyring_service": str(cfg.get("broker_keyring_service") or "meicagent"),
    }
