"""Tastytrade CLI for EarningsAgent. All commands print JSON to stdout.

Usage:
  python src/tt.py secrets_status
  python src/tt.py secrets_set [--keys client_secret refresh_token account_number]
  python src/tt.py get_connection_status
  python src/tt.py list_accounts
  python src/tt.py get_account_info [--account_number X]
  python src/tt.py get_quote --symbol AAPL
  python src/tt.py get_option_chain --symbol AAPL [--expiration YYYY-MM-DD]
      [--include_greeks] [--include_quotes] [--include_oi] [--include_volume]
      [--strike_count N] [--around_price F]
  python src/tt.py get_market_metrics --symbol AAPL
  python src/tt.py execute_trade --order '<JSON>' [--account_number X] [--live]

NOTE: This file exceeds 500-line guideline (529 lines). Documented exception
in docs/file-size-exceptions.md. Broker API wrapper with tightly coupled
session/auth/order functions. Split would complicate credential handling.
The shared read-side primitives (account resolution, option-chain strike
helpers) now live in cherrypit.broker (src/_core); the order write path and
CLI response shaping stay here.

Adapted from MEICAgent's src/tt.py, with the stream-cache layer removed --
this project has no persistent streamer daemon (its scan cadence is once a
day, not every few minutes), so every call goes straight to a live DXLink
connection or REST, at the cost of a few seconds of latency per call that
MEICAgent avoids via its cache. That tradeoff is fine here: `get_candidates`
runs once, not in a tight loop.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date
from typing import Any

# Allow running as `python src/tt.py` from any working directory, and make the cherrypit-core
# submodule (src/_core) importable *before* the `from cherrypit import ...` lines below — mirroring
# credentials.py's bootstrap, so a standalone CLI run doesn't depend on credentials being imported
# first (import-sorting puts the cherrypit imports ahead of the local `import credentials`).
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "_core"))

from cherrypit import broker as _broker
from cherrypit import dxfeed as _dx

import credentials as _creds
from session import get_session


def _load_config() -> dict:
    root = os.path.join(os.path.dirname(__file__), "..")
    path = os.path.join(root, "config", "config.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def _live_trading_enabled() -> bool:
    cfg = _load_config()
    if "enable_live_trading" in cfg:
        return bool(cfg["enable_live_trading"])
    return os.environ.get("ENABLE_LIVE_TRADING", "").lower() in ("1", "true", "yes")


def _out(data: Any) -> None:
    print(json.dumps(data, default=str))


def _error(exc: Exception) -> dict:
    msg = f"{type(exc).__name__}: {exc}"
    retryable = any(f" {c} " in str(exc) or str(exc).endswith(str(c)) for c in (500, 502, 503, 504))
    result: dict = {"ok": False, "error": msg}
    if retryable:
        result["retryable"] = True
    return result


def _serialize(obj: Any) -> Any:
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


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _get_account(account_number: str | None = None):
    # Delegates to cherrypit.broker (src/_core). The stored account number is passed in as the
    # default so the core stays decoupled from this module's credentials shim.
    return await _broker.resolve_account(
        get_session(), account_number,
        default_number=_creds.get_secret(_creds.ACCOUNT_NUMBER),
    )


# Thin re-exports of the shared option-chain helpers now implemented in cherrypit.broker (src/_core).
_strike = _broker.strike_of
_nearest_expiration = _broker.nearest_expiration
_atm_window = _broker.atm_window


# On-demand DXLink collectors — thin shims over cherrypit.dxfeed (src/_core). Passing get_session() in
# as an argument preserves this module's deliberate design point: a missing-credentials CredentialError
# surfaces to the caller (not swallowed as a fake feed timeout), because the session is built here,
# outside the shared collector's broad except.
async def _collect_events(event_cls, symbols: list[str], timeout: float, extract=None) -> dict:
    return await _dx.collect_events(get_session(), event_cls, symbols, timeout, extract=extract)


async def _collect_greeks(symbols: list[str], timeout: float) -> dict:
    return await _dx.collect_greeks(get_session(), symbols, timeout)


async def _collect_quotes(symbols: list[str], timeout: float) -> dict:
    return await _dx.collect_quotes(get_session(), symbols, timeout)


async def _collect_open_interest(symbols: list[str], timeout: float) -> dict:
    return await _dx.collect_open_interest(get_session(), symbols, timeout)


async def _collect_last_prices(symbols: list[str], timeout: float) -> dict:
    return await _dx.collect_last_prices(get_session(), symbols, timeout)


async def _collect_option_volume(symbols: list[str], timeout: float) -> dict:
    return await _dx.collect_option_volume(get_session(), symbols, timeout)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_get_connection_status(_args) -> dict:
    status: dict = {
        "ok": True,
        "live_trading_enabled": _live_trading_enabled(),
        "credentials_present": _creds.secrets_present(),
    }
    if not status["credentials_present"]:
        status["ok"] = False
        status["hint"] = "Run `python src/tt.py secrets_set` to store credentials."
        return status
    try:
        session = get_session()
        status["account_count"] = await _broker.account_count(session)
        status["connected"] = True
    except Exception as exc:
        status["ok"] = False
        status["connected"] = False
        status.update(_error(exc))
    return status


async def cmd_list_accounts(_args) -> dict:
    try:
        return {"ok": True, "accounts": await _broker.list_accounts(get_session())}
    except Exception as exc:
        return _error(exc)


async def cmd_get_account_info(args) -> dict:
    try:
        account = await _get_account(getattr(args, "account_number", None))
        session = get_session()
        balances = await account.get_balances(session)
        return {
            "ok": True,
            "account_number": account.account_number,
            "nickname": getattr(account, "nickname", None),
            "balances": _serialize(balances),
        }
    except Exception as exc:
        return _error(exc)


async def cmd_get_quote(args) -> dict:
    try:
        sym = args.symbol.strip().upper()
        timeout = getattr(args, "timeout", 6.0)
        trades = await _collect_last_prices([sym], timeout)
        last = trades.get(sym)
        result: dict = {"ok": True, "symbol": sym, "price": last}
        if last is None:
            result["note"] = "DXLink trade feed did not respond within the timeout."
        return result
    except Exception as exc:
        return _error(exc)


async def cmd_get_market_metrics(args) -> dict:
    try:
        from tastytrade.metrics import get_market_metrics
        symbol = args.symbol.strip().upper()
        session = get_session()
        metrics = await get_market_metrics(session, [symbol])
        if not metrics:
            return {"ok": False, "error": f"no market metrics for {symbol}"}
        m = metrics[0]
        return {"ok": True, "symbol": symbol, "market_cap": _num(m.market_cap)}
    except Exception as exc:
        return _error(exc)


async def cmd_get_option_chain(args) -> dict:
    try:
        from tastytrade.instruments import get_option_chain

        symbol = args.symbol.strip().upper()
        expiration_filter = getattr(args, "expiration", None)
        session = get_session()
        chain = await get_option_chain(session, symbol)
        if not chain:
            return {"ok": False, "error": f"No option chain for {symbol}."}

        expirations = sorted(chain.keys())
        include_greeks = getattr(args, "include_greeks", False)
        include_quotes = getattr(args, "include_quotes", False)
        include_oi = getattr(args, "include_oi", False)
        include_volume = getattr(args, "include_volume", False)
        strike_count = getattr(args, "strike_count", 15)
        around_price = getattr(args, "around_price", None)
        greeks_timeout = getattr(args, "greeks_timeout", 6.0)
        quotes_timeout = getattr(args, "quotes_timeout", 6.0)
        oi_timeout = getattr(args, "oi_timeout", 10.0)
        volume_timeout = getattr(args, "volume_timeout", 10.0)

        if expiration_filter:
            want = date.fromisoformat(expiration_filter)
            if want not in chain:
                return {
                    "ok": False,
                    "error": f"No {expiration_filter} expiration for {symbol}.",
                    "available_expirations": [str(e) for e in expirations],
                }
            selected = [want]
        elif include_greeks or include_quotes or include_oi or include_volume:
            selected = [_nearest_expiration(expirations)]
        else:
            selected = expirations

        options_by_exp = {exp: list(chain[exp]) for exp in selected}
        if strike_count is not None:
            options_by_exp = {
                exp: _atm_window(opts, strike_count, around_price)
                for exp, opts in options_by_exp.items()
            }

        serialized = {
            str(exp): [_serialize(o) for o in opts]
            for exp, opts in options_by_exp.items()
        }

        result: dict = {"ok": True, "symbol": symbol, "chain": serialized}

        if include_greeks:
            streamer_symbols = [
                o.streamer_symbol
                for opts in options_by_exp.values()
                for o in opts
                if getattr(o, "streamer_symbol", None)
            ]
            greeks = await _collect_greeks(streamer_symbols, greeks_timeout)
            received = 0
            for entries in serialized.values():
                for entry in entries:
                    g = greeks.get(entry.get("streamer_symbol"))
                    if g is not None:
                        entry["delta"] = _num(g.delta)
                        entry["gamma"] = _num(g.gamma)
                        entry["theta"] = _num(g.theta)
                        entry["iv"] = _num(g.volatility)
                        received += 1
            result["greeks_included"] = True
            result["greeks_complete"] = received == len(streamer_symbols)
            result["greeks_received"] = received

        if include_quotes:
            streamer_symbols = [
                o.streamer_symbol
                for opts in options_by_exp.values()
                for o in opts
                if getattr(o, "streamer_symbol", None)
            ]
            quotes = await _collect_quotes(streamer_symbols, quotes_timeout)
            received = 0
            for entries in serialized.values():
                for entry in entries:
                    q = quotes.get(entry.get("streamer_symbol"))
                    if q is not None:
                        bid = _num(q.bid_price)
                        ask = _num(q.ask_price)
                        entry["bid"] = bid
                        entry["ask"] = ask
                        entry["mid"] = round((bid + ask) / 2, 4) if bid is not None and ask is not None else None
                        received += 1
            result["quotes_included"] = True
            result["quotes_complete"] = received == len(streamer_symbols)
            result["quotes_received"] = received

        if include_oi:
            streamer_symbols = [
                o.streamer_symbol
                for opts in options_by_exp.values()
                for o in opts
                if getattr(o, "streamer_symbol", None)
            ]
            oi = await _collect_open_interest(streamer_symbols, oi_timeout)
            received = 0
            for entries in serialized.values():
                for entry in entries:
                    val = oi.get(entry.get("streamer_symbol"))
                    if val is not None:
                        entry["open_interest"] = int(val)
                        received += 1
            result["oi_included"] = True
            result["oi_complete"] = received == len(streamer_symbols)
            result["oi_received"] = received

        if include_volume:
            streamer_symbols = [
                o.streamer_symbol
                for opts in options_by_exp.values()
                for o in opts
                if getattr(o, "streamer_symbol", None)
            ]
            vol = await _collect_option_volume(streamer_symbols, volume_timeout)
            received = 0
            for entries in serialized.values():
                for entry in entries:
                    val = vol.get(entry.get("streamer_symbol"))
                    if val is not None:
                        entry["day_volume"] = int(val)
                        received += 1
            result["volume_included"] = True
            result["volume_complete"] = received == len(streamer_symbols)
            result["volume_received"] = received

        return result
    except Exception as exc:
        return _error(exc)


def _build_order(spec: dict):
    # Delegates to cherrypit.broker (src/_core): pure order construction (dict spec -> NewOrder),
    # no submission. cmd_execute_trade below still owns the dry-run/live place_order call.
    return _broker.build_order(spec)


async def cmd_execute_trade(args) -> dict:
    live = getattr(args, "live", False)
    if live and not _live_trading_enabled():
        return {"ok": False, "error": "Live trading is disabled. Set enable_live_trading=true in config.json."}
    try:
        spec = json.loads(args.order)
        account = await _get_account(getattr(args, "account_number", None))
        order = _build_order(spec)
        # cherrypit.broker owns the preflight-then-optionally-live submission core (src/_core);
        # it places a live order only when live=True and the dry-run preflight had no errors.
        return await _broker.place_order(account, get_session(), order, live=live,
                                         serialize=_serialize)
    except Exception as exc:
        return _error(exc)


def cmd_secrets_status(_args) -> dict:
    status = _creds.secrets_status()
    return {
        "ok": True,
        "secrets": {k: ("set" if v else "missing") for k, v in status.items()},
        "ready": all(status[k] for k in (_creds.CLIENT_SECRET, _creds.REFRESH_TOKEN)),
    }


def cmd_secrets_set(args) -> dict:
    """Interactively prompt for and store credentials in the OS keyring."""
    import getpass

    label = {
        _creds.CLIENT_SECRET: "Client Secret",
        _creds.REFRESH_TOKEN: "Refresh Token",
        _creds.ACCOUNT_NUMBER: "Account Number (optional — press Enter to skip)",
    }
    updated = []
    skipped = []

    keys_to_set = args.keys if getattr(args, "keys", None) else list(_creds.ALL_SECRETS)

    for key in keys_to_set:
        current = _creds.get_secret(key)
        hint = " [already set — press Enter to keep]" if current else ""
        prompt = f"  {label.get(key, key)}{hint}: "
        value = getpass.getpass(prompt)
        if not value:
            if current or key not in _creds.REQUIRED_SECRETS:
                skipped.append(key)
            else:
                return {"ok": False, "error": f"{key} is required"}
        else:
            _creds.set_secret(key, value)
            updated.append(key)

    return {"ok": True, "updated": updated, "skipped": skipped}


_ASYNC_COMMANDS = {
    "get_connection_status", "list_accounts", "get_account_info",
    "get_quote", "get_option_chain", "execute_trade", "get_market_metrics",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="EarningsAgent tastytrade CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("get_connection_status")
    sub.add_parser("list_accounts")
    sub.add_parser("secrets_status")

    p_secrets = sub.add_parser("secrets_set")
    p_secrets.add_argument("--keys", nargs="*", default=None)

    p_ai = sub.add_parser("get_account_info")
    p_ai.add_argument("--account_number", default=None)

    p_quote = sub.add_parser("get_quote")
    p_quote.add_argument("--symbol", required=True)

    p_chain = sub.add_parser("get_option_chain")
    p_chain.add_argument("--symbol", required=True)
    p_chain.add_argument("--expiration", default=None)
    p_chain.add_argument("--include_greeks", action="store_true")
    p_chain.add_argument("--include_quotes", action="store_true")
    p_chain.add_argument("--include_oi", action="store_true")
    p_chain.add_argument("--include_volume", action="store_true")
    p_chain.add_argument("--strike_count", type=int, default=15)
    p_chain.add_argument("--around_price", type=float, default=None)

    p_metrics = sub.add_parser("get_market_metrics")
    p_metrics.add_argument("--symbol", required=True)

    p_exec = sub.add_parser("execute_trade")
    p_exec.add_argument("--order", required=True)
    p_exec.add_argument("--account_number", default=None)
    p_exec.add_argument("--live", action="store_true")

    args = parser.parse_args()
    dispatch = {
        "get_connection_status": cmd_get_connection_status,
        "list_accounts": cmd_list_accounts,
        "secrets_status": cmd_secrets_status,
        "secrets_set": cmd_secrets_set,
        "get_account_info": cmd_get_account_info,
        "get_quote": cmd_get_quote,
        "get_option_chain": cmd_get_option_chain,
        "execute_trade": cmd_execute_trade,
        "get_market_metrics": cmd_get_market_metrics,
    }
    handler = dispatch[args.command]
    if args.command in _ASYNC_COMMANDS:
        result = asyncio.run(handler(args))
    else:
        result = handler(args)
    _out(result)


if __name__ == "__main__":
    main()

