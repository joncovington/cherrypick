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

from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager

_managers: dict[str, SessionManager] = {}


def reset() -> None:
    """Drop cached sessions so the next `get_session` builds a fresh one.

    A session binds its async transport to the first event loop that drives it, and every
    `asyncio.run` in the CLI creates *and closes* its own loop. A session cached across two such
    calls therefore carries a transport bound to a closed loop, and the second call dies with
    `RuntimeError: Event loop is closed` — which the desk reports as a preflight failure, making
    `propose` impossible. Call this immediately before entering a new loop.
    """
    _managers.clear()


def get_session(cfg: dict[str, Any]) -> Any:
    """A cached OAuth session for the configured broker keyring service.

    The cache is only valid *within* one event loop — see `reset`, which every broker-touching CLI
    helper calls before its `asyncio.run`.

    Falls back read-only to the suite-wide shared service (the single credential source), so a
    machine onboarded once needs no per-module entry — borrowing credentials is still not
    borrowing permissions; desk authorization remains its own config + PIN + ticket.
    """
    service = str(cfg.get("broker_keyring_service") or "meicagent")
    if service not in _managers:
        legacy = () if service == SHARED_SERVICE else (SHARED_SERVICE,)
        _managers[service] = SessionManager(CredentialStore(service, legacy_service_names=legacy))
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
