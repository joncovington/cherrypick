"""cherrypick.core.broker.cli -- the READ-ONLY broker CLI verbs the modules' seams share.

Two families, because the suite has two CLI shapes and the orchestrator parses both:

* **`tt.py` (meic, earnings).** `error_result`, `connection_check`, `account_list`, `account_info`.
  Each takes the module's own session factory, account resolver and flags as arguments, so a test
  that patches `tt.get_session` still reaches it through the module's wrapper.
* **`broker_cli.py` (bwb, flies).** `connection_status`, `list_accounts`, `order_status`,
  `cancel_order`, `secrets_set`, `secrets_status`. Each takes the module's `credentials` module (its
  `store`, `SERVICE_NAME`, `get_session`, `designated_account`) as `creds`.

Nothing here places an order. `cancel_order` pulls an existing one, which needs no live gate: the
order it targets could only exist because a prior submission passed one. `execute_trade` and every
module's `live_gates` stay in the modules, and this file holds no path to `place_order`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from cherrypick.core import broker as _broker

_RETRYABLE_STATUS = (500, 502, 503, 504)


# --------------------------------------------------------------------------- tt.py shape
def error_result(exc: Exception) -> dict:
    """One JSON error object for a failed verb, `retryable: True` when the broker's message carries a
    5xx status."""
    msg = f"{type(exc).__name__}: {exc}"
    retryable = any(f" {c} " in str(exc) or str(exc).endswith(str(c)) for c in _RETRYABLE_STATUS)
    result: dict = {"ok": False, "error": msg}
    if retryable:
        result["retryable"] = True
    return result


async def connection_check(
    get_session: Callable[[], Any], *, credentials_present: bool, live_trading_enabled: bool, hint: str
) -> dict:
    """Credentials present, then a session and an account count. Not ok without credentials."""
    status: dict = {
        "ok": True,
        "live_trading_enabled": live_trading_enabled,
        "credentials_present": credentials_present,
    }
    if not status["credentials_present"]:
        status["ok"] = False
        status["hint"] = hint
        return status
    try:
        session = get_session()
        status["account_count"] = await _broker.account_count(session)
        status["connected"] = True
    except Exception as exc:
        status["ok"] = False
        status["connected"] = False
        status.update(error_result(exc))
    return status


async def account_list(get_session: Callable[[], Any]) -> dict:
    """FULL account numbers: the orchestrator's designation flow writes one to keyring (it masks
    for display)."""
    try:
        return {"ok": True, "accounts": await _broker.list_accounts(get_session())}
    except Exception as exc:
        return error_result(exc)


async def account_info(
    get_account: Callable[[str | None], Awaitable[Any]],
    get_session: Callable[[], Any],
    account_number: str | None,
    serialize: Callable[[Any], Any],
) -> dict:
    try:
        account = await get_account(account_number)
        session = get_session()
        balances = await account.get_balances(session)
        return {
            "ok": True,
            "account_number": account.account_number,
            "nickname": getattr(account, "nickname", None),
            "balances": serialize(balances),
        }
    except Exception as exc:
        return error_result(exc)


# --------------------------------------------------------------------------- broker_cli.py shape
async def designated_account(creds: Any, session: Any) -> Any:
    return await _broker.resolve_account(session, creds.designated_account())


async def connection_status(creds: Any, *, live_trading_enabled: bool, hint: str) -> dict:
    """Keyring + session probe, the designated account masked. Without credentials it is NOT ok: it
    returns the hint and does not try a session -- `connection_check`'s answer, so every module's
    probe means the same thing. Until 2026-10-12 this said `ok: True` with nothing stored (bwb's
    behaviour, carried into the fold), and `doctor` and `connect`, which read `ok` as connected,
    reported a module with no secrets as connected."""
    secrets = creds.store.secrets_status()
    status: dict = {
        "ok": True,
        "service": creds.SERVICE_NAME,
        "credentials_present": creds.store.secrets_present(),
        "secrets": secrets,
        "live_trading_enabled": live_trading_enabled,
    }
    designated = creds.designated_account()
    status["designated"] = "****" + designated[-4:] if designated else None
    if not status["credentials_present"]:
        status["ok"] = False
        status["connected"] = False
        status["hint"] = hint
        return status
    try:
        session = creds.get_session()
        status["account_count"] = await _broker.account_count(session)
        status["connected"] = True
    except Exception as exc:  # noqa: BLE001
        status["ok"] = False
        status["connected"] = False
        status["error"] = f"{type(exc).__name__}: {exc}"
    return status


async def list_accounts(creds: Any) -> dict:
    """FULL account numbers, the tt.py machine shape the orchestrator's designation flow reads."""
    session = creds.get_session()
    return {"ok": True, "accounts": await _broker.list_accounts(session)}


async def order_status(creds: Any, order_id: Any) -> dict:
    """Read-only: checking a working order's status places nothing."""
    session = creds.get_session()
    account = await designated_account(creds, session)
    return await _broker.order_status(account, session, order_id)


async def cancel_order(creds: Any, order_id: Any) -> dict:
    """Cancelling is not a new live submission (no live gate): the working order it targets could
    only exist because a prior submission passed the gate, and refusing to pull it is less safe."""
    session = creds.get_session()
    account = await designated_account(creds, session)
    return await _broker.cancel_order(account, session, order_id)


def secrets_set(creds: Any, keys) -> dict:
    """Hidden-input secrets flow, argv-compatible with tt.py's so the orchestrator's `connect` drives
    every module identically. Blank input keeps a stored value."""
    import getpass

    for key in keys:
        value = getpass.getpass(f"{key} (input hidden, blank to keep current): ").strip()
        if value:
            creds.store.set_secret(key, value)
    return secrets_status(creds)


def secrets_status(creds: Any) -> dict:
    return {"ok": True, "service": creds.SERVICE_NAME, "secrets": creds.store.secrets_status()}
