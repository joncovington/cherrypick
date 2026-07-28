#!/usr/bin/env python3
"""Flies broker CLI — the thin seam over cherrypick.core.broker (live scaffold).

Quotes keep coming from the shared stream cache (the provider); this module exists only for
the broker-side operations the live plan needs: connection check, account listing, and order
preflight/placement through `core.broker.place_order` (real dry-run preflight + the deploy
governor). It is deliberately tiny — everything reusable already lives in core.

**Live submission is double-gated and OFF.** `--live` requires BOTH `live.enabled: true` in
this module's config AND a non-empty `live.gate0_confirmed` attestation (who/when Gate 0 of
docs/live-trading-plan.md was judged passed). Until then every submission is a dry run —
the real preflight against the real designated account, placing nothing.

Usage:
    python src/broker_cli.py get_connection_status
    python src/broker_cli.py list_accounts
    python src/broker_cli.py execute_trade --order '<spec JSON>'          # dry-run preflight
    python src/broker_cli.py execute_trade --order '<spec JSON>' --live   # gated, see above
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "_core")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from cherrypick.core import broker as _broker  # noqa: E402

import credentials as creds  # noqa: E402
from cli import load_config  # noqa: E402


def live_gates(config: dict) -> list[str]:
    """The unmet gates for a LIVE submission — empty means live is allowed. Pure."""
    live = config.get("live") or {}
    unmet = []
    if not live.get("enabled"):
        return ["live.enabled is false (docs/live-trading-plan.md, Gate 0 first)"]
    if not str(live.get("gate0_confirmed") or "").strip():
        unmet.append("live.gate0_confirmed is empty — a human must attest Gate 0 passed (who/when)")
    return unmet


async def _account(session):
    return await _broker.resolve_account(session, creds.designated_account())


async def cmd_connection_status(_args) -> dict:
    session = creds.get_session()
    n = await _broker.account_count(session)
    return {"ok": True, "accounts": n,
            "designated": ("****" + d[-4:]) if (d := creds.designated_account()) else None}


async def cmd_list_accounts(_args) -> dict:
    session = creds.get_session()
    accounts = await _broker.list_accounts(session)
    designated = creds.designated_account()
    for a in accounts:
        full = a.pop("account_number", "") or ""
        a["account"] = "****" + full[-4:] if full else "?"
        a["designated"] = bool(designated and full == designated)
    return {"ok": True, "accounts": accounts}


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
    return await _broker.place_order(account, session, order, live=bool(args.live),
                                     deploy_limit_pct=limit)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("get_connection_status")
    sub.add_parser("list_accounts")
    et = sub.add_parser("execute_trade")
    et.add_argument("--order", required=True)
    et.add_argument("--live", action="store_true")
    args = ap.parse_args()
    fn = {"get_connection_status": cmd_connection_status,
          "list_accounts": cmd_list_accounts,
          "execute_trade": cmd_execute_trade}[args.cmd]
    try:
        result = asyncio.run(fn(args))
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, default=str))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
