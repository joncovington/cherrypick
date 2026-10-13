#!/usr/bin/env python3
"""Flies broker CLI — the thin seam over cherrypick.core.broker (live scaffold).

Quotes keep coming from the shared stream cache (the provider); this module exists only for
the broker-side operations the live plan needs: connection check, account listing, and order
preflight/placement through `core.broker.place_order` (real dry-run preflight + the deploy
governor). It is deliberately tiny — everything reusable already lives in core.

**Live submission is gated and OFF.** `--live` requires `live.enabled: true` in this module's
config, a non-empty `live.gate0_confirmed` attestation (who/when Gate 0 of
docs/live-trading-plan.md was judged passed), `live.arm` naming a configured arm, a designated
account, and today's arm record from `/live-flies-start` -- every gate the live tick checks
(`live_gates`). Without them every submission is a dry run -- the real preflight against the
real designated account, placing nothing.

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
import sys

from cherrypick.core import broker as _broker  # noqa: E402
from cherrypick.core import settlement as _settlement  # noqa: E402
from cherrypick.core.broker import cli as _broker_cli

from cherrypick.flies import credentials as creds  # noqa: E402
from cherrypick.flies import provider  # noqa: E402
from cherrypick.flies.cli import load_config  # noqa: E402


async def fresh_option_quotes(session, symbols: list[str]) -> dict[str, dict]:
    """One-shot REST market-data snapshot for OCC option symbols — a plain `GET
    /market-data/by-type` (batches every symbol into one call), no DXLink handshake, no
    subscribe/listen loop. Used only to reprice a live entry immediately before submission (see
    live_loop.py's fresh-quote check), where the cached stream-cache snapshot can diverge from
    what the broker's real-time execution-quality check considers marketable. Kept local rather
    than added to core.broker: this module's own docstring commits it to staying "a thin,
    dependency-free seam over core.broker," and core.broker lives in a separate, suite-shared git
    submodule (pinned to the same commit across every package) — a genuinely reusable primitive
    belongs there eventually, but that's a deliberate cross-suite change, not a side effect of
    this module's own bugfix.

    Returns `{occ_symbol: {bid, ask, mid, updated_at}}` for symbols with usable (non-crossed,
    positive-ask) data; a symbol absent from the result means the call returned nothing sane for
    it — the caller (entry_fresh_reprice) treats a missing leg as "can't confirm the price," never
    a reason to fall back to the stale cached one."""
    from tastytrade.market_data import get_market_data_by_type

    if not symbols:
        return {}
    rows = await get_market_data_by_type(session, options=symbols)
    out: dict[str, dict] = {}
    for row in rows:
        bid, ask = row.bid, row.ask
        if bid is None or ask is None:
            continue
        bid, ask = float(bid), float(ask)
        if ask <= 0 or bid < 0 or bid > ask:
            continue
        mid = float(row.mid) if row.mid is not None else (bid + ask) / 2.0
        out[row.symbol] = {
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
    return out


# The settlement-price chain moved to `cherrypick.core.settlement` on 2026-09-18 (bwb is its second
# consumer). Kept as a module attribute here so the adapter and the tests keep one seam to patch.
official_settlement_price = _settlement.official_index_close


_READ = object()  # "read it yourself": the keyring for the account, the ET clock for the day


def live_gates(config: dict, *, designated=_READ, when=None) -> list[str]:
    """The unmet gates for a LIVE submission -- empty means live is allowed. Re-checked by the
    execution adapter on every live submit and by `execute_trade --live` before it builds anything.

    One source, two additions (2026-10-12). The account gates are `live_loop.readiness` itself, so
    the tick and the submission cannot drift: `live.enabled`, the Gate 0 attestation, `live.arm`
    naming a configured arm, and a designated account. Before this the by-hand path and the
    per-submit re-check skipped the last two -- and with no designated account
    `core.broker.resolve_account` falls back to the FIRST account on the login, so a by-hand
    `--live` could have traded an account nobody designated. Then the day's arm record, read
    exactly as the tick's dead-man's switch reads it (`live_loop.should_disarm`): a day nobody
    armed with `/live-flies-start`, or one past `live.disarm_time`, refuses every live submission,
    not only the scheduled tick. Disabled short-circuits before any keyring or file read.

    `designated` and `when` exist for tests and for the adapter, which has its own account read;
    left out, they are read here."""
    from cherrypick.flies import live_loop  # lazy: live_loop reaches this module lazily too

    live = config.get("live") or {}
    if not live.get("enabled"):
        return ["live.enabled is false (docs/live-trading-plan.md, Gate 0 first)"]
    if designated is _READ:
        designated = creds.designated_account()
    unmet = live_loop.readiness(config, designated=designated)
    when = when or provider.now_et()
    reason = live_loop.should_disarm(config, provider.minute_of_day(when), when.date().isoformat())
    if reason:
        unmet.append(f"not armed for this session: {reason} (arm with /live-flies-start)")
    return unmet


async def _account(session):
    return await _broker_cli.designated_account(creds, session)


async def cmd_connection_status(_args) -> dict:
    """Keyring + session probe, designated account masked (`core.broker.cli`)."""
    return await _broker_cli.connection_status(
        creds,
        live_trading_enabled=bool((load_config().get("live") or {}).get("enabled")),
        hint="run `cherrypick connect --module flies` to store the OAuth secrets",
    )


async def cmd_list_accounts(_args) -> dict:
    # FULL account numbers, the tt.py machine shape the designation flow reads.
    return await _broker_cli.list_accounts(creds)


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
    return await _broker_cli.order_status(creds, args.order_id)


async def cmd_cancel_order(args) -> dict:
    """Pulling a working order is not a new live submission, so no live gate (`core.broker.cli`)."""
    return await _broker_cli.cancel_order(creds, args.order_id)


def cmd_secrets_set(args) -> dict:
    """Hidden-input secrets flow, argv-compatible with tt.py's. Blank input keeps a stored value."""
    return _broker_cli.secrets_set(creds, args.keys)


def cmd_secrets_status(_args) -> dict:
    return _broker_cli.secrets_status(creds)


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
                "get_connection_status": cmd_connection_status,
                "list_accounts": cmd_list_accounts,
                "execute_trade": cmd_execute_trade,
                "order_status": cmd_order_status,
                "cancel_order": cmd_cancel_order,
            }[args.cmd]
            result = asyncio.run(fn(args))
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, default=str))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
