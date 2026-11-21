"""What a live path's new purchases may spend in a cash account: settled cash. Pure.

In a cash account (an IRA is one), a purchase paid for with an unsettled sale's proceeds and sold
before those proceeds settle is a good-faith violation, and a few in twelve months restrict the account.
The broker names no settled-cash figure, and tastytrade says its buying power is not settled cash, so
`settled_cash` takes the smaller of the cash it would let be withdrawn (an unsettled sale's proceeds
cannot be withdrawn) and the equity buying power, then takes off, again, the caller's own sales that
have not settled. That last step may count them twice. It is meant to: a purchase that waits a day
costs the caller an entry, a violation costs the account. A margin account has no such limit. An
account whose kind or figures cannot be read is treated as a cash account with no cash.

The figures come from `cherrypick.core.broker.cash_funds` (through `execution.Broker.cash_funds`); the
unsettled sales come from the caller's own ledger, since only it knows which sales were its own. What
waits when the limit is short is the caller's to decide: a purchase that opens risk waits, and nothing
that closes risk ever does.
"""

from __future__ import annotations

__all__ = ["settled_cash"]


def settled_cash(funds: dict | None, unsettled_proceeds: float) -> dict:
    """What new purchases may spend in the account.

    `funds` is a `cash_funds` read (or None when it failed); `unsettled_proceeds` the caller's own
    sales that have not settled. Returns {"kind", "readable", "limit", "reason", ...}: `limit` None
    means no settled-cash limit (a margin account); a number is the dollars new purchases may spend,
    never below 0. No read, or a kind that is neither margin nor cash, is unreadable with limit 0."""
    if not funds:
        return {
            "kind": None,
            "readable": False,
            "limit": 0.0,
            "reason": "cash_unreadable: the account's cash could not be read",
        }
    kind = str(funds.get("margin_or_cash") or "").strip().lower()
    if kind == "margin":
        return {"kind": "margin", "readable": True, "limit": None, "reason": None}
    if kind != "cash":
        return {
            "kind": kind or None,
            "readable": False,
            "limit": 0.0,
            "reason": f"cash_unreadable: account kind {funds.get('margin_or_cash')!r} is not margin or cash",
        }
    try:
        withdrawable = float(funds["cash_available_to_withdraw"])
        buying_power = float(funds["equity_buying_power"])
    except (KeyError, TypeError, ValueError):
        return {
            "kind": "cash",
            "readable": False,
            "limit": 0.0,
            "reason": "cash_unreadable: the cash account's figures could not be read",
        }
    unsettled = max(0.0, float(unsettled_proceeds or 0.0))
    return {
        "kind": "cash",
        "readable": True,
        "limit": round(max(0.0, min(withdrawable, buying_power) - unsettled), 2),
        "reason": None,
        "withdrawable": withdrawable,
        "equity_buying_power": buying_power,
        "unsettled_proceeds": round(unsettled, 2),
    }
