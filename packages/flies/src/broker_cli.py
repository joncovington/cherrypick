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


def _serialize(obj):
    """Turn a tastytrade SDK object (or a nested structure of them) into a JSON-safe plain value.
    `core.broker.place_order`'s `response`/`preflight` fields are the raw SDK objects unless a
    caller supplies this — every `place_order` call in this module MUST pass it, or the caller's
    `result["response"]["order"]["id"]` lookup silently finds nothing (the bug that let the live
    loop resubmit the same entry every tick without ever recording it: `place()` in live_loop.py
    read the order id back out of this same shape and got `{}` every time). Same shape as MEIC's
    `tt.py::_serialize` — kept local rather than shared so this module stays a thin, dependency-free
    seam over core.broker, per its own docstring."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    return str(obj)


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


_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; cherrypick-flies settlement fetch)"}
_HTTP_TIMEOUT_SECONDS = 8


async def _tastytrade_index_price(session, symbol: str) -> float | None:
    """The index's own close (once posted), falling back to last/mark if `close` hasn't populated
    yet — tastytrade's `indices=[...]` market-data call, same REST endpoint `fresh_option_quotes`
    uses for options. Never raises: any SDK/network error is treated as "this source came up
    empty," letting the caller move to the next one."""
    from tastytrade.market_data import get_market_data_by_type

    try:
        rows = await get_market_data_by_type(session, indices=[symbol])
    except Exception:  # noqa: BLE001 — one source of several; caller falls through on failure
        return None
    for row in rows:
        for candidate in (row.close, row.last, row.mark):
            if candidate is not None and float(candidate) > 0:
                return float(candidate)
    return None


def _yahoo_index_price(symbol: str) -> float | None:
    """Yahoo Finance's public chart JSON endpoint (not HTML scraping — a plain structured API
    response), keyed by the `^`-prefixed index ticker. Blocking; called via `asyncio.to_thread`."""
    import json
    import urllib.request

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/%5E{symbol}?interval=1d&range=1d"
    try:
        req = urllib.request.Request(url, headers=_HTTP_HEADERS)
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            data = json.load(resp)
        meta = data["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        return float(price) if price else None
    except Exception:  # noqa: BLE001 — one source of several; caller falls through on failure
        return None


def _barchart_index_price(symbol: str) -> float | None:
    """Barchart's quote page embeds its data as inline JSON (`"lastPrice":"..."`) rather than
    requiring JS rendering — a plain regex pull, not a browser-driven scrape. Blocking; called via
    `asyncio.to_thread`. Last resort: no structured API, most exposed to the page changing shape."""
    import re
    import urllib.request

    url = f"https://www.barchart.com/stocks/quotes/${symbol}/performance"
    try:
        req = urllib.request.Request(url, headers=_HTTP_HEADERS)
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        match = re.search(r'"lastPrice":"([0-9.]+)"', html)
        return float(match.group(1)) if match else None
    except Exception:  # noqa: BLE001 — last source; caller reports "no_source_available"
        return None


async def official_settlement_price(session, symbol: str) -> tuple[float | None, str]:
    """Best-effort fetch of the day's settlement/closing index level for `symbol` (XSP/SPX), tried
    in order: tastytrade's own index quote, then Yahoo Finance, then Barchart. Returns
    `(price, source)` on success (`source` names which one answered, for the settlement log), or
    `(None, "no_source_available")` if every source came up empty — the caller must never guess a
    settlement price; that stays a hard refusal, same as a missing fresh option quote.

    Deliberately not gated behind a fixed post-close delay: the real settlement print isn't
    guaranteed to exist the instant the market closes, so this is designed to be called on every
    live tick until one succeeds (see `live_loop.run_settle_live`), not just once."""
    price = await _tastytrade_index_price(session, symbol)
    if price is not None:
        return price, "tastytrade"
    price = await asyncio.to_thread(_yahoo_index_price, symbol)
    if price is not None:
        return price, "yahoo"
    price = await asyncio.to_thread(_barchart_index_price, symbol)
    if price is not None:
        return price, "barchart"
    return None, "no_source_available"


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
    return {
        "ok": True,
        "accounts": n,
        "designated": ("****" + d[-4:]) if (d := creds.designated_account()) else None,
    }


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
    return await _broker.place_order(
        account, session, order, live=bool(args.live), serialize=_serialize, deploy_limit_pct=limit
    )


async def cmd_order_status(args) -> dict:
    """Read-only — no live gating needed, checking a working order's status places nothing."""
    session = creds.get_session()
    account = await _account(session)
    return await _broker.order_status(account, session, args.order_id)


async def cmd_cancel_order(args) -> dict:
    """Cancelling a live order is not itself a new live submission (no --live gate here) —
    the working order it targets could only exist because a prior live submission already
    passed the gate; refusing to let it be pulled would be strictly less safe."""
    session = creds.get_session()
    account = await _account(session)
    return await _broker.cancel_order(account, session, args.order_id)


def cmd_secrets_set(args) -> dict:
    """Hidden-input secrets flow, argv-compatible with tt.py's so the orchestrator's `connect`
    drives both modules identically. Blank input keeps a stored value."""
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
