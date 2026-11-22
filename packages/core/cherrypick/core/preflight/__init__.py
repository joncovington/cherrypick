"""Broker dry-runs of orders a person is weighing: what each would take in buying power and fees,
and whether the broker would refuse it. Nothing here can place an order.

Two locks keep it that way, and the tests hold both:

* every submission is `Broker.place(spec, live=False)`, a literal `False` (a source scan in
  `tests/test_preflight.py` fails on any other form); and
* the broker is `core.live.account_alerts`' read-only adapter, whose live gates are never met, so
  even a live call through it would be refused before the broker saw it.

`review(trades, broker)` takes plain trades:

    {"name": "SPY Feb 580/560P", "price": 1.61, "price_effect": "debit",
     "legs": [{"symbol": ".SPY270219P580", "action": "buy to open", "quantity": 1},
              {"symbol": "SPY   270219P00560000", "action": "sell to open", "quantity": 1}]}

A leg's symbol is OCC or streamer for an option, or a plain ticker for shares. The result per trade
is the broker's buying-power effect (`bpr` is what the order would consume: the broker's change,
sign flipped), its fee calculation, its warnings, and on a refusal its reason. The account appears
masked only.

The broker's preflight judges an order against the account as it stands at the call, so a set of
trades is judged one at a time, never as a combined book.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Any

from cherrypick.core import streamcache
from cherrypick.core.redact import mask_account, redact_accounts

ACTIONS = ("buy to open", "sell to open", "buy to close", "sell to close")


def occ_symbol(symbol: str) -> str:
    """The OCC form of an option symbol given as OCC or streamer; "" for anything else."""
    s = str(symbol).strip()
    if s.startswith("."):
        return streamcache.streamer_to_occ_symbol(s)
    if len(s) > 6 and streamcache.occ_to_streamer_symbol(s):
        return s
    return ""


def order_leg(symbol: str, action: str, quantity: int = 1) -> dict:
    """One leg of a `core.broker.build_order` spec. An option is an `Equity Option` (index options
    included: the broker files XSP and SPX under the same type), anything else `Equity`."""
    act = str(action).strip().lower()
    if act not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}, not {action!r}")
    qty = int(quantity)
    if qty <= 0:
        raise ValueError(f"quantity must be positive, not {quantity!r}")
    occ = occ_symbol(symbol)
    if occ:
        return {"instrument_type": "Equity Option", "symbol": occ, "action": act, "quantity": qty}
    return {
        "instrument_type": "Equity",
        "symbol": str(symbol).strip().upper(),
        "action": act,
        "quantity": qty,
    }


def order_spec(trade: dict) -> dict:
    """The `build_order` spec for one trade: a Day limit at `price` with its `price_effect`."""
    legs = [
        order_leg(leg["symbol"], leg["action"], leg.get("quantity", 1)) for leg in trade.get("legs") or []
    ]
    if not legs:
        raise ValueError(f"trade {trade.get('name')!r} has no legs")
    effect = str(trade.get("price_effect", "debit")).strip().lower()
    if effect not in ("debit", "credit"):
        raise ValueError(f"price_effect must be debit or credit, not {effect!r}")
    price = trade.get("price")
    if price is None:
        raise ValueError(f"trade {trade.get('name')!r} has no limit price")
    return {
        "time_in_force": str(trade.get("time_in_force", "Day")),
        "order_type": "Limit",
        "legs": legs,
        "price": abs(float(price)),
        "price_effect": effect,
    }


def _dec(value: Any) -> float | None:
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _messages(items) -> list[str]:
    out = []
    for item in items or []:
        if isinstance(item, dict):
            code, msg = item.get("code"), item.get("message")
            out.append(f"{code}: {msg}" if code else str(msg))
        else:
            out.append(str(item))
    return out


def summarize(name: str, result: dict) -> dict:
    """The person-facing view of one `Broker.place(..., live=False)` result."""
    response = result.get("response") or {}
    bpe = response.get("buying_power_effect") or {}
    if not bpe and result.get("buying_power"):
        bpe = result["buying_power"]
    fees = response.get("fee_calculation") or {}
    change = _dec(bpe.get("change_in_buying_power"))
    out = {
        "name": name,
        "accepted": bool(result.get("ok")),
        "dry_run": True,
        "account": mask_account(result.get("account_number")) if result.get("account_number") else None,
        "bpr": -change if change is not None else None,
        "current_buying_power": _dec(bpe.get("current_buying_power")),
        "new_buying_power": _dec(bpe.get("new_buying_power")),
        "margin_requirement_change": _dec(bpe.get("change_in_margin_requirement")),
        "is_spread": bpe.get("is_spread"),
        "fees": -(_dec(fees.get("total_fees")) or 0.0) if fees else None,
        "fee_detail": {k: _dec(v) for k, v in fees.items()} if fees else {},
        "warnings": _messages(response.get("warnings") or (result.get("buying_power") or {}).get("warnings")),
        "errors": _messages(response.get("errors")) + _messages(result.get("problems")),
        "refusal": None,
    }
    if not out["accepted"]:
        out["refusal"] = redact_accounts(str(result.get("error") or "refused")).strip()
    return out


def review(trades: Iterable[dict], broker: Any) -> list[dict]:
    """Dry-run each trade through `broker` and summarize it. A trade that cannot be built (a bad
    symbol, action or price) is reported as refused with the reason, and the rest still run."""
    out = []
    for trade in trades:
        name = str(trade.get("name") or "trade")
        try:
            spec = order_spec(trade)
        except (KeyError, TypeError, ValueError) as exc:
            out.append({"name": name, "accepted": False, "dry_run": True, "refusal": f"not built: {exc}"})
            continue
        result = broker.place(spec, live=False)
        if result.get("dry_run") is False:  # never expected: the adapter's own contract says dry run
            raise RuntimeError("preflight received a live result; stopping")
        out.append(summarize(name, result))
    return out


def make_broker(
    *, keyring_service: str | None = None, login: str | None = None, account_last4: str | None = None
):
    """The read-only adapter on the account to judge against: a keyring service's designated
    account, or an extra broker login's (one account, or the one ending `account_last4`). Its live
    gates are never met."""
    from cherrypick.core.live import account_alerts

    if login:
        return account_alerts.make_login_broker(login, account_last4)
    if not keyring_service:
        raise ValueError("name the account: a keyring service, or a login")
    return account_alerts.make_broker(keyring_service)
