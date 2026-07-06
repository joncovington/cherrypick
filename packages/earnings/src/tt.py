"""Tastytrade CLI for EarningsAgent. All commands print JSON to stdout.

Usage:
  python src/tt.py secrets_status
  python src/tt.py secrets_set [--keys client_secret refresh_token account_number]
  python src/tt.py get_connection_status
  python src/tt.py list_accounts
  python src/tt.py get_account_info [--account_number X]
  python src/tt.py get_quote --symbol AAPL
  python src/tt.py get_option_chain --symbol AAPL [--expiration YYYY-MM-DD]
      [--include_greeks] [--include_quotes] [--include_oi] [--strike_count N] [--around_price F]
  python src/tt.py execute_trade --order '<JSON>' [--account_number X] [--live]

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
from decimal import Decimal
from typing import Any

# Allow running as `python src/tt.py` from any working directory.
sys.path.insert(0, os.path.dirname(__file__))

import credentials as _creds
from session import get_session


def _load_config() -> dict:
    root = os.path.join(os.path.dirname(__file__), "..")
    path = os.path.join(root, "config.json")
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
    from tastytrade.account import Account
    session = get_session()
    number = account_number or _creds.get_secret(_creds.ACCOUNT_NUMBER)
    if number:
        return await Account.get(session, number)
    accounts = await Account.get(session)
    if not accounts:
        raise RuntimeError("No accounts found for these credentials.")
    return accounts[0]


def _nearest_expiration(expirations: list[date], target_days: int = 0) -> date:
    today = date.today()
    return min(expirations, key=lambda e: abs((e - today).days - target_days))


def _strike(option: Any) -> float | None:
    try:
        return float(option.strike_price)
    except (TypeError, ValueError):
        return None


def _atm_window(options: list, strike_count: int, around_price: float | None) -> list:
    strikes = sorted({s for s in (_strike(o) for o in options) if s is not None})
    if not strikes:
        return options
    center = around_price if around_price is not None else strikes[len(strikes) // 2]
    nearest = min(range(len(strikes)), key=lambda i: abs(strikes[i] - center))
    lo = max(0, nearest - strike_count)
    hi = min(len(strikes), nearest + strike_count + 1)
    keep = set(strikes[lo:hi])
    return [o for o in options if _strike(o) in keep]


async def _collect_events(event_cls, symbols: list[str], timeout: float, extract=None) -> dict:
    from tastytrade import DXLinkStreamer
    out: dict = {}
    symbols = [s for s in symbols if s]
    if not symbols:
        return out

    async def _drain(streamer):
        remaining = set(symbols)
        async for event in streamer.listen(event_cls):
            value = extract(event) if extract else event
            if value is not None:
                out[event.event_symbol] = value
            remaining.discard(event.event_symbol)
            if not remaining:
                return

    # get_session() is deliberately outside the broad except below: a missing-
    # credentials CredentialError must propagate to the caller so it's reported
    # as a real error, not swallowed and misreported as "feed didn't respond
    # in time" (caught during testing -- get_quote with no stored credentials
    # returned {"ok": true, "price": null, "note": "...timeout..."}, which is
    # wrong; the actual reason was no session at all).
    session = get_session()
    try:
        async with DXLinkStreamer(session) as streamer:
            await streamer.subscribe(event_cls, symbols)
            await asyncio.wait_for(_drain(streamer), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    except Exception:
        pass
    return out


async def _collect_greeks(symbols: list[str], timeout: float) -> dict:
    from tastytrade.dxfeed import Greeks
    return await _collect_events(Greeks, symbols, timeout)


async def _collect_quotes(symbols: list[str], timeout: float) -> dict:
    from tastytrade.dxfeed import Quote
    return await _collect_events(Quote, symbols, timeout)


async def _collect_open_interest(symbols: list[str], timeout: float) -> dict:
    """Summary events carry open_interest. Verified live: these fire on a
    fresh on-demand subscribe within a few seconds, no persistent daemon
    needed -- unlike MEICAgent's own comment ("OI only comes from Summary --
    no live fallback"), which turned out to describe an architecture choice
    MEICAgent made (only ever consuming Summary via its persistent streamer),
    not a real limitation of the DXLink feed itself.
    """
    from tastytrade.dxfeed import Summary
    return await _collect_events(Summary, symbols, timeout, extract=lambda e: e.open_interest)


async def _collect_last_prices(symbols: list[str], timeout: float) -> dict:
    from tastytrade.dxfeed import Trade
    return await _collect_events(Trade, symbols, timeout, extract=lambda e: _num(e.price))


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
        from tastytrade.account import Account
        session = get_session()
        accounts = await Account.get(session)
        status["connected"] = True
        status["account_count"] = len(accounts)
    except Exception as exc:
        status["ok"] = False
        status["connected"] = False
        status.update(_error(exc))
    return status


async def cmd_list_accounts(_args) -> dict:
    try:
        from tastytrade.account import Account
        session = get_session()
        accounts = await Account.get(session)
        return {
            "ok": True,
            "accounts": [
                {
                    "account_number": a.account_number,
                    "nickname": getattr(a, "nickname", None),
                    "account_type": getattr(a, "account_type_name", None),
                }
                for a in accounts
            ],
        }
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
        strike_count = getattr(args, "strike_count", 15)
        around_price = getattr(args, "around_price", None)
        greeks_timeout = getattr(args, "greeks_timeout", 6.0)
        quotes_timeout = getattr(args, "quotes_timeout", 6.0)
        oi_timeout = getattr(args, "oi_timeout", 10.0)

        if expiration_filter:
            want = date.fromisoformat(expiration_filter)
            if want not in chain:
                return {
                    "ok": False,
                    "error": f"No {expiration_filter} expiration for {symbol}.",
                    "available_expirations": [str(e) for e in expirations],
                }
            selected = [want]
        elif include_greeks or include_quotes or include_oi:
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

        return result
    except Exception as exc:
        return _error(exc)


_ACTION_MAP = {
    "buy to open": "BUY_TO_OPEN",
    "sell to open": "SELL_TO_OPEN",
    "buy to close": "BUY_TO_CLOSE",
    "sell to close": "SELL_TO_CLOSE",
}


def _build_order(spec: dict):
    from tastytrade.order import NewOrder, OrderAction, OrderTimeInForce, OrderType, Leg

    tif = OrderTimeInForce(str(spec.get("time_in_force", "Day")))
    otype = OrderType(str(spec.get("order_type", "Limit")))
    legs = []
    for leg in spec.get("legs", []):
        action = OrderAction[_ACTION_MAP[str(leg["action"]).strip().lower()]]
        legs.append(Leg(
            instrument_type=leg["instrument_type"],
            symbol=leg["symbol"],
            action=action,
            quantity=Decimal(str(leg["quantity"])),
        ))
    kwargs: dict = {"time_in_force": tif, "order_type": otype, "legs": legs}
    if spec.get("price") is not None:
        price = Decimal(str(spec["price"]))
        effect = spec.get("price_effect")
        if effect is not None:
            magnitude = abs(price)
            price = -magnitude if str(effect).strip().lower() == "credit" else magnitude
        kwargs["price"] = price
    return NewOrder(**kwargs)


async def cmd_execute_trade(args) -> dict:
    live = getattr(args, "live", False)
    if live and not _live_trading_enabled():
        return {"ok": False, "error": "Live trading is disabled. Set enable_live_trading=true in config.json."}
    try:
        spec = json.loads(args.order)
        account = await _get_account(getattr(args, "account_number", None))
        session = get_session()
        order = _build_order(spec)
        dry_run = not live

        preflight = await account.place_order(session, order, dry_run=True)
        errors = [str(e) for e in (getattr(preflight, "errors", None) or [])]
        warnings = [str(w) for w in (getattr(preflight, "warnings", None) or [])]
        bpe = getattr(preflight, "buying_power_effect", None)
        bp_summary: dict = {"warnings": warnings}
        if bpe:
            bp_summary.update({
                "current_buying_power": str(getattr(bpe, "current_buying_power", None)),
                "new_buying_power": str(getattr(bpe, "new_buying_power", None)),
                "change_in_buying_power": str(getattr(bpe, "change_in_buying_power", None)),
            })
        if errors:
            return {"ok": False, "error": "pre-flight validation failed", "problems": errors, "buying_power": bp_summary}

        if dry_run:
            return {"ok": True, "dry_run": True, "account_number": account.account_number,
                     "buying_power": bp_summary, "response": _serialize(preflight)}

        response = await account.place_order(session, order, dry_run=False)
        return {"ok": True, "dry_run": False, "account_number": account.account_number,
                 "buying_power": bp_summary, "response": _serialize(response)}
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
    "get_quote", "get_option_chain", "execute_trade",
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
    p_chain.add_argument("--strike_count", type=int, default=15)
    p_chain.add_argument("--around_price", type=float, default=None)

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
    }
    handler = dispatch[args.command]
    if args.command in _ASYNC_COMMANDS:
        result = asyncio.run(handler(args))
    else:
        result = handler(args)
    _out(result)


if __name__ == "__main__":
    main()
