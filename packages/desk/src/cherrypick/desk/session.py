"""Broker session for the desk — borrowed credentials, never its own.

The desk stores **no broker secrets**. It opens a session through an existing module's keyring
service (`desk.broker_keyring_service`, default `meicagent`), because duplicating OAuth secrets into
a second keyring entry would double the number of places a credential can leak from while adding no
capability — the account is the same account either way.

Borrowing the *credentials* is not borrowing the *permissions*: the desk never reads that module's
`enable_live_trading`, `account_deploy_limit_pct`, or any other trading flag, and the module's loop
has no idea the desk exists. Authorization for a desk order comes only from `desk.json` + the PIN +
the ticket. `tests/test_isolation.py` pins that separation.
"""

from __future__ import annotations

from typing import Any

from cherrypick.core.auth import CredentialStore, SessionManager

_managers: dict[str, SessionManager] = {}


def get_session(cfg: dict[str, Any]) -> Any:
    """A cached OAuth session for the configured broker keyring service."""
    service = str(cfg.get("broker_keyring_service") or "meicagent")
    if service not in _managers:
        _managers[service] = SessionManager(CredentialStore(service))
    return _managers[service].get_session()


def serialize(obj: Any) -> Any:
    """Best-effort JSON-safe view of a tastytrade response object, matching what the modules' own
    `tt.py` produces so desk output reads the same as theirs."""
    for attr in ("model_dump", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:  # noqa: BLE001 — fall through to the next strategy
                pass
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return obj
