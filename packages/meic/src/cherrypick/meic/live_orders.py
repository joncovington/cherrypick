"""Live order construction for the MEIC live loop — pure translation, no submission.

Turns a paper.py decision (the same `chosen` candidate `evaluate_entry` returns, or the same
`decision` dict `evaluate_open_trade` returns) into a `tt.py execute_trade` order spec. Nothing
here talks to a broker, a DB, or a file — `live_loop.py` owns submission and recording.

Three order shapes (IC entry is the only entry shape live v1 supports — see docs/reference.md's "MEIC
has no profit-target close" and the live-loop plan's rung-1 scope: no ORB, no multi-symbol):

  entry_spec        4-leg IC open (2x sell-to-open, 2x buy-to-open), Day limit at the net
                    credit floored to the tick (asks for slightly less — conservative, and
                    index options tick in nickels so the preflight can't reject the increment).
                    Mirrors live_smoke.py's spec_from_strategy pricing exactly.
  stop_close_spec   2-leg close of one side (BTC the short, STC the long), Day limit at the
                    marketable crossing price times docs/reference.md's documented `stop_limit_ratio`
                    cushion `(short_ask - long_bid) * stop_limit_ratio`, ceilinged to the tick
                    so the cushion can't be rounded back below marketable.
  force_close_spec  Same crossing-price pricing as stop_close_spec, over whichever side(s) of
                    the IC are still open (one or both) — the 2-leg and 4-leg force-close cases
                    are the same code path with a different leg set.

**Every leg symbol is OCC (2026-10-12).** The candidate and the ledger row carry only the DXLink
streamer symbol (`.XSP261012P580`) -- the stream cache's key. tastytrade orders take the OCC symbol
(`XSP   261012P00580000`), which is what `live_smoke` already sent. Until this date all three
builders sent the streamer symbol, so no live meic order could have been accepted. `_order_symbol`
converts at this boundary, through `cherrypick.core.streamcache.streamer_to_occ_symbol` (the
checked inverse of the cache's own converter), and refuses a symbol with no OCC form.
"""

from __future__ import annotations

from cherrypick.core.streamcache import occ_to_streamer_symbol, streamer_to_occ_symbol
from cherrypick.core.structures import TICK, tick_ceil, tick_floor  # one rounding rule, suite-wide

__all__ = ["TICK", "order_symbol", "tick_ceil", "tick_floor"]


def order_symbol(symbol: str | None) -> str:
    """The OCC symbol a broker order names this leg by. A streamer symbol is converted; an OCC
    symbol passes through unchanged (only when it converts back to itself); anything else is a
    ValueError -- a leg the broker cannot identify is never sent."""
    occ = streamer_to_occ_symbol(symbol or "")
    if occ:
        return occ
    if symbol and streamer_to_occ_symbol(occ_to_streamer_symbol(symbol)) == symbol:
        return symbol
    raise ValueError(f"leg symbol {symbol!r} has no OCC form -- not an order symbol")


def _leg(rec: dict, action: str, quantity: int) -> dict:
    symbol = rec.get("streamer_symbol")
    if not symbol:
        raise ValueError(f"leg at strike {rec.get('strike')!r} carries no streamer_symbol")
    return {
        "instrument_type": rec.get("instrument_type") or "Equity Option",
        "symbol": order_symbol(symbol),
        "action": action,
        "quantity": quantity,
    }


def _close_legs(short_sym: str, long_sym: str, quantity) -> list[dict]:
    """BTC the short, STC the long -- one side of an open IC, named by OCC symbol."""
    return [
        {
            "instrument_type": "Equity Option",
            "symbol": order_symbol(short_sym),
            "action": "buy to close",
            "quantity": quantity,
        },
        {
            "instrument_type": "Equity Option",
            "symbol": order_symbol(long_sym),
            "action": "sell to close",
            "quantity": quantity,
        },
    ]


def entry_spec(chosen: dict, quantity: int = 1) -> dict:
    """The 4-leg IC entry from an `evaluate_entry`-admitted `chosen` candidate."""
    price = tick_floor(chosen["net_credit"])
    if price <= 0:
        raise ValueError(f"entry credit {chosen['net_credit']!r} floors to nothing submittable")
    return {
        "time_in_force": "Day",
        "order_type": "Limit",
        "price": price,
        "price_effect": "credit",
        "legs": [
            _leg(chosen["short_put"], "sell to open", quantity),
            _leg(chosen["long_put"], "buy to open", quantity),
            _leg(chosen["short_call"], "sell to open", quantity),
            _leg(chosen["long_call"], "buy to open", quantity),
        ],
    }


def _side_legs(trade: dict, leg_quotes: dict, side: str) -> tuple:
    """(short_symbol, long_symbol, short_quote, long_quote) for one side of an open IC."""
    if side == "put":
        short_sym, long_sym = trade["put_symbol"], trade["long_put_symbol"]
    elif side == "call":
        short_sym, long_sym = trade["call_symbol"], trade["long_call_symbol"]
    else:
        raise ValueError(f"side must be 'put' or 'call', got {side!r}")
    sq, lq = leg_quotes.get(short_sym), leg_quotes.get(long_sym)
    if not sq or not lq or sq.get("ask") is None or lq.get("bid") is None:
        raise ValueError(f"no usable quote to close the {side} side (symbols {short_sym}/{long_sym})")
    return short_sym, long_sym, sq, lq


def stop_close_spec(trade: dict, side: str, leg_quotes: dict, stop_limit_ratio: float = 1.02) -> dict:
    """Close one side of an open IC: BTC the short, STC the long. Day limit at the marketable
    crossing price `(short_ask - long_bid) * stop_limit_ratio`, ceilinged to the tick."""
    short_sym, long_sym, sq, lq = _side_legs(trade, leg_quotes, side)
    price = tick_ceil((sq["ask"] - lq["bid"]) * stop_limit_ratio)
    if price <= 0:
        raise ValueError(f"{side} close price {price!r} is not a submittable debit")
    return {
        "time_in_force": "Day",
        "order_type": "Limit",
        "price": price,
        "price_effect": "debit",
        "legs": _close_legs(short_sym, long_sym, trade.get("quantity", 1)),
    }


def force_close_spec(
    trade: dict,
    leg_quotes: dict,
    stop_limit_ratio: float = 1.02,
    *,
    put_open: bool = True,
    call_open: bool = True,
) -> dict:
    """Close whichever side(s) of the IC are still open (one or both) at once. Same marketable
    crossing-price pricing as `stop_close_spec`, summed across the sides being closed."""
    if not put_open and not call_open:
        raise ValueError("force_close_spec called with nothing open to close")
    legs, raw_debit = [], 0.0
    qty = trade.get("quantity", 1)
    for side, open_ in (("put", put_open), ("call", call_open)):
        if not open_:
            continue
        short_sym, long_sym, sq, lq = _side_legs(trade, leg_quotes, side)
        raw_debit += sq["ask"] - lq["bid"]
        legs.extend(_close_legs(short_sym, long_sym, qty))
    price = tick_ceil(raw_debit * stop_limit_ratio)
    if price <= 0:
        raise ValueError(f"force-close price {price!r} is not a submittable debit")
    return {
        "time_in_force": "Day",
        "order_type": "Limit",
        "price": price,
        "price_effect": "debit",
        "legs": legs,
    }
