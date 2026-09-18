#!/usr/bin/env python3
"""bwb broker CLI -- the thin seam the orchestrator's `connect`/`account` onboarding drives, and
the live gates the live loop re-checks on every submission. LIVE path only: the paper loop never
imports this module.

Commands (JSON on stdout, the tt.py machine shape the orchestrator already parses):

    get_connection_status   # keyring + session probe; designated account masked
    list_accounts           # FULL account numbers -- the designation flow writes one to keyring
    secrets_status
    secrets_set --keys client_secret refresh_token   # hidden input, blank keeps the stored value
    execute_trade --order '<spec json>' [--live]     # by-hand seam test; gated like the loop
    order_status --order_id <id>
    cancel_order --order_id <id>

Everything broker-shaped is `cherrypick.core.broker` (submission with the deploy governor, the
serializer, the order lifecycle); nothing here duplicates it. There is deliberately no fresh-quote
fetch: bwb's ~7-DTE structure has produced no spread-checker rejection to answer, and the streamer-
before-API rule says not to add one speculatively.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from cherrypick.core import broker as _broker

from cherrypick.bwb import credentials as creds
from cherrypick.bwb import engine
from cherrypick.bwb.cli import load_config


def live_gates(config: dict) -> list[str]:
    """The unmet gates for a LIVE submission -- empty means live is allowed. Pure, and re-checked
    by the execution adapter on every live submit (not only at loop start).

    `live.arm` must name one of the four base books: the wall book and every advised twin are
    paper experiments whose structures are not the pilot's, and a typo here must refuse rather
    than trade something else."""
    live = config.get("live") or {}
    if not live.get("enabled"):
        return ["live.enabled is false"]
    unmet = []
    if not str(live.get("gate0_confirmed") or "").strip():
        unmet.append("live.gate0_confirmed is empty -- a human must attest Gate 0 passed (who/when)")
    arm = str(live.get("arm") or "").strip()
    if arm not in engine.BOOKS:
        unmet.append(f"live.arm {arm!r} is not a base book (one of {', '.join(engine.BOOKS)})")
    return unmet


async def _account(session):
    return await _broker.resolve_account(session, creds.designated_account())


async def cmd_get_connection_status(_args) -> dict:
    secrets = creds.store.secrets_status()
    status: dict = {
        "ok": True,
        "service": creds.SERVICE_NAME,
        "credentials_present": creds.store.secrets_present(),
        "secrets": secrets,
        "live_trading_enabled": bool((load_config().get("live") or {}).get("enabled")),
    }
    designated = creds.designated_account()
    status["designated"] = ("****" + designated[-4:]) if designated else None
    if not status["credentials_present"]:
        status["hint"] = "run `cherrypick connect --module bwb` to store the OAuth secrets"
        return status
    try:
        session = creds.get_session()
        status["account_count"] = await _broker.account_count(session)
    except Exception as exc:  # noqa: BLE001 -- a probe reports, it never raises
        status["ok"] = False
        status["error"] = f"{type(exc).__name__}: {exc}"
    return status


async def cmd_list_accounts(_args) -> dict:
    # Machine shape, matching MEIC's tt.py: FULL account numbers, because the orchestrator's
    # account-designation flow needs them to write the keyring value (it masks for display).
    session = creds.get_session()
    return {"ok": True, "accounts": await _broker.list_accounts(session)}


async def cmd_execute_trade(args) -> dict:
    config = load_config()
    if args.live:
        unmet = live_gates(config)
        if unmet:
            return {"ok": False, "error": "live submission gated", "unmet_gates": unmet}
    spec = json.loads(args.order)
    session = creds.get_session()
    account = await _account(session)
    order = _broker.build_order(spec)
    limit = (config.get("live") or {}).get("account_deploy_limit_pct") or None
    return await _broker.place_order(account, session, order, live=bool(args.live), deploy_limit_pct=limit)


async def cmd_order_status(args) -> dict:
    """Read-only -- checking a working order's status places nothing."""
    session = creds.get_session()
    account = await _account(session)
    return await _broker.order_status(account, session, args.order_id)


async def cmd_cancel_order(args) -> dict:
    """Cancelling is not a new live submission (no --live gate): the working order it targets could
    only exist because a prior submission passed the gate, and refusing to pull it is less safe."""
    session = creds.get_session()
    account = await _account(session)
    return await _broker.cancel_order(account, session, args.order_id)


def cmd_secrets_set(args) -> dict:
    """Hidden-input secrets flow, argv-compatible with tt.py's so the orchestrator's `connect`
    drives every module identically. Blank input keeps a stored value."""
    import getpass

    for key in args.keys:
        value = getpass.getpass(f"{key} (input hidden, blank to keep current): ").strip()
        if value:
            creds.store.set_secret(key, value)
    return {"ok": True, "service": creds.SERVICE_NAME, "secrets": creds.store.secrets_status()}


def cmd_secrets_status(_args) -> dict:
    return {"ok": True, "service": creds.SERVICE_NAME, "secrets": creds.store.secrets_status()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("get_connection_status")
    sub.add_parser("list_accounts")
    sub.add_parser("secrets_status")
    ss = sub.add_parser("secrets_set")
    ss.add_argument("--keys", nargs="+", default=["client_secret", "refresh_token"])
    et = sub.add_parser("execute_trade")
    et.add_argument("--order", required=True)
    et.add_argument("--live", action="store_true")
    os_ = sub.add_parser("order_status")
    os_.add_argument("--order_id", required=True)
    co = sub.add_parser("cancel_order")
    co.add_argument("--order_id", required=True)
    args = ap.parse_args()
    sync = {"secrets_set": cmd_secrets_set, "secrets_status": cmd_secrets_status}
    try:
        if args.cmd in sync:
            result = sync[args.cmd](args)
        else:
            fn = {
                "get_connection_status": cmd_get_connection_status,
                "list_accounts": cmd_list_accounts,
                "execute_trade": cmd_execute_trade,
                "order_status": cmd_order_status,
                "cancel_order": cmd_cancel_order,
            }[args.cmd]
            result = asyncio.run(fn(args))
    except Exception as exc:  # noqa: BLE001 -- one JSON error object, never a traceback on stdout
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, default=str))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
