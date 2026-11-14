"""The ladder's rule, pure: where the next lot buys, where each lot sells, and how big a lot is.

Item 26's rule (research/edge-hypotheses-plan.md) as the Discord group trades it: buy a lot 7% under
the reference close, sell each lot on its own at +4% of its cost, no stop, no time limit. Two entry
rules, both item 26's:

- `once`: at most one new lot a session, at 93% of the previous session's close.
- `chain`: with no lot open, the first lot at 93% of the previous session's close; then each
  further lot at 93% of the last lot bought, in the same session or later. The group's posted
  fills (11-13: 65.53 x 0.93^2 and x 0.93^3) are this rule.

Two exit rules:

- `fixed`: 104% of the lot's cost.
- `decay`: item 27's passing change: 104% of cost to day 30, the breakeven after costs from day 31,
  95% of cost from day 121 (calendar days from the lot's buy session).

Prices are limit prices, so they round to the cent: a buy level rounds down, a target rounds up, so
a rounded order never fills on worse terms than the rule's own number.
"""

from __future__ import annotations

import math
from datetime import date

from cherrypick.core import fees as _fees

ENTRY_RULES = ("once", "chain")
TARGET_RULES = ("fixed", "decay")

DROP = 0.07
GAIN = 0.04
DECAY_HOLD_DAYS = 30  # through day 30: the full gain
DECAY_BREAKEVEN_DAYS = 120  # days 31-120: breakeven after costs
DECAY_FLOOR = 0.95  # from day 121: 95% of cost


def floor_cent(x: float) -> float:
    return math.floor(round(x * 100, 6)) / 100.0


def ceil_cent(x: float) -> float:
    return math.ceil(round(x * 100, 6)) / 100.0


def buy_level(
    rule: str, *, ref_close: float | None, last_buy: float | None, drop: float = DROP
) -> float | None:
    """The next lot's limit price, or None when there is no reference to price it from.

    `last_buy` is the most recent lot bought while lots are still open (None when the arm holds
    none on this name), which only `chain` reads."""
    if rule not in ENTRY_RULES:
        raise ValueError(f"unknown entry rule {rule!r}")
    base = last_buy if (rule == "chain" and last_buy is not None) else ref_close
    if base is None or base <= 0:
        return None
    return floor_cent(base * (1 - drop))


def lot_shares(lot_dollars: float, price: float) -> int:
    """Whole shares a lot buys at `price`: never more than `lot_dollars`."""
    if price <= 0:
        return 0
    return int(math.floor(lot_dollars / price + 1e-9))


def breakeven(cost: float, shares: int) -> float:
    """The lowest cent price at which selling `shares` bought at `cost` nets at least zero after the
    sale's pass-through fees (buys carry none at tastytrade)."""
    p = ceil_cent(cost)
    while p * shares - _fees.stock_trade_fee(shares, p, side="sell") < cost * shares - 1e-9:
        p = round(p + 0.01, 2)
    return p


def age_days(bought: date, today: date) -> int:
    return (today - bought).days


def target(rule: str, *, cost: float, shares: int, age: int, gain: float = GAIN) -> float:
    """A lot's sell limit at `age` calendar days."""
    if rule not in TARGET_RULES:
        raise ValueError(f"unknown target rule {rule!r}")
    if rule == "fixed" or age <= DECAY_HOLD_DAYS:
        return ceil_cent(cost * (1 + gain))
    if age <= DECAY_BREAKEVEN_DAYS:
        return breakeven(cost, shares)
    return ceil_cent(cost * DECAY_FLOOR)


def target_stage(rule: str, age: int) -> str:
    if rule == "fixed" or age <= DECAY_HOLD_DAYS:
        return "gain"
    return "breakeven" if age <= DECAY_BREAKEVEN_DAYS else "floor"


def money(shares: int, buy: float, sell: float) -> dict:
    """A closed lot's money, signed cash flow, whole-position dollars. Fills are at the limit, so
    slippage is zero by construction (paper's stated assumption); settlement is zero for shares."""
    entry_value = round(-shares * buy, 2)
    exit_value = round(shares * sell, 2)
    gross = round(entry_value + exit_value, 2)
    fee = _fees.stock_trade_fee(shares, buy, side="buy") + _fees.stock_trade_fee(shares, sell, side="sell")
    fee = round(fee, 2)
    return {
        "entry_value": entry_value,
        "exit_value": exit_value,
        "gross_pnl": gross,
        "fees": fee,
        "settlement": 0.0,
        "slippage": 0.0,
        "net_pnl": round(gross - fee, 2),
        "net_pct": round(100.0 * (gross - fee) / (shares * buy), 4) if shares and buy else None,
    }
