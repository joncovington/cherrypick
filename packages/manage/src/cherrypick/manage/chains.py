"""Rebuild each open position's ORDER CHAIN from the broker's trade history.

The positions endpoint keeps one average open price per leg, which is right for a position opened in
one order and wrong for one that has been rolled: the credit a roll took in lives on legs that are
closed now, and vanishes from the basis. GLD on 2026-10-13 was opened for 8.33 db and rolled twice
for 6.40 cr -- 1.93 db in all -- while its open legs alone said 11.04 db.

A chain is found the way the broker groups it. Start from an open leg; take the orders that built its
current position (since the symbol was last flat); and for every order taken, follow what it touched --
a leg it closed leads back to the orders that opened that leg, a leg it opened leads forward to the
orders that closed it. Open legs whose chains share an order are one trade.

A chain is only used when it RECONCILES: replaying its own orders (and any expiry or assignment
deliveries on its symbols) must leave exactly the quantities held today. History that starts after
the position did, or a leg the broker moved without an order, fails that check, and the caller falls
back to the open legs' prices and says so -- a basis that does not add up is worse than none.

Pure: legs and history rows in, chains out.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

FEE_FIELDS = ("commission", "clearing_fees", "regulatory_fees", "proprietary_index_option_fees")


def _f(v: Any) -> float:
    try:
        out = float(v)
    except (TypeError, ValueError):
        return 0.0
    return out if math.isfinite(out) else 0.0


def _sym(row: dict[str, Any]) -> str:
    return " ".join(str(row.get("symbol") or "").split())


def _signed(row: dict[str, Any]) -> float:
    """Position change: a buy adds, a sell subtracts (to open or to close alike)."""
    action = str(row.get("action") or row.get("transaction_sub_type") or "").lower()
    qty = _f(row.get("quantity"))
    return qty if action.startswith("buy") else -qty


def _closes(row: dict[str, Any]) -> bool:
    return "close" in str(row.get("action") or "").lower()


def _role(rows: list[dict[str, Any]], first: bool) -> str:
    closes = any(_closes(r) for r in rows)
    opens = any(not _closes(r) for r in rows)
    if first:
        return "open"
    if closes and opens:
        return "roll"
    return "close" if closes else "add"


def _merge_fills(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One line per leg of an order: partial fills of the same action and symbol summed, at their
    quantity-weighted price (AMZN's roll filled its 2x short 285C as two one-lot fills)."""
    merged: dict[tuple, dict[str, Any]] = {}
    for r in rows:
        key = (r.get("action"), _sym(r))
        qty, px = _f(r.get("quantity")), _f(r.get("price"))
        m = merged.setdefault(
            key, {"action": r.get("action"), "symbol": _sym(r), "quantity": 0.0, "notional": 0.0}
        )
        m["quantity"] += qty
        m["notional"] += qty * px
    return [
        {
            "action": m["action"],
            "symbol": m["symbol"],
            "quantity": m["quantity"],
            "price": m["notional"] / m["quantity"] if m["quantity"] else 0.0,
        }
        for m in merged.values()
    ]


def _order_rows(ordered: list[Any], by_order: dict[Any, list[dict]]) -> list[dict[str, Any]]:
    rows = []
    for i, o in enumerate(ordered):
        legs_in = by_order[o]
        fills = _merge_fills(legs_in)
        rows.append(
            {
                "order_id": o,
                "executed_at": str(legs_in[0].get("executed_at")),
                "role": _role(legs_in, i == 0),
                "net": sum(_f(r.get("value")) for r in legs_in),
                "fees": sum(_f(r.get(k)) for r in legs_in for k in FEE_FIELDS),
                "unit": math.gcd(*[int(f["quantity"]) or 1 for f in fills]),
                "legs": fills,
            }
        )
    return rows


def from_orders(order_ids: list[Any], history: list[dict[str, Any]], symbols: list[str]) -> dict[str, Any]:
    """A chain made of the given orders, in `build`'s layout, for orders some other reading has linked
    and reconciled (the PMCC view's: a long call and every short call sold against it, whatever
    orders they were sold in). `reconciled` is the caller's word, not checked again here."""
    wanted = set(order_ids)
    by_order: dict[Any, list[dict]] = defaultdict(list)
    for r in history:
        if r.get("transaction_type") == "Trade" and r.get("order_id") in wanted:
            by_order[r["order_id"]].append(r)
    ordered = sorted(by_order, key=lambda o: (min(str(r.get("executed_at")) for r in by_order[o]), str(o)))
    rows = _order_rows(ordered, by_order)
    return {
        "symbols": sorted(symbols),
        "orders": rows,
        "cash": sum(r["net"] for r in rows),
        "fees": sum(r["fees"] for r in rows),
        "reconciled": True,
    }


def build(legs: list[dict[str, Any]], history: list[dict[str, Any]]) -> dict[str, Any]:
    """{"chains": [...], "unchained": [leg symbols no reconciled chain explains]}.

    Each chain: orders (oldest first, each with role, net cash and fees), the open leg symbols it
    explains, its net cash (signed, a credit received is +) and fees (negative), and `reconciled`.
    """
    trades = [r for r in history if r.get("transaction_type") == "Trade" and r.get("order_id") is not None]
    deliveries = [r for r in history if r.get("transaction_type") == "Receive Deliver"]
    trades.sort(key=lambda r: (str(r.get("executed_at")), str(r.get("id"))))
    by_sym: dict[str, list[dict]] = defaultdict(list)
    by_order: dict[Any, list[dict]] = defaultdict(list)
    for r in trades:
        by_sym[_sym(r)].append(r)
        by_order[r["order_id"]].append(r)
    moves: dict[str, list[dict]] = defaultdict(list)  # trades and deliveries, for "last flat"
    for r in sorted(trades + deliveries, key=lambda r: (str(r.get("executed_at")), str(r.get("id")))):
        moves[_sym(r)].append(r)

    def openers_before(symbol: str, before: str | None) -> list[Any]:
        pos, orders = 0.0, []
        for r in moves.get(symbol, []):
            if before is not None and str(r.get("executed_at")) >= before:
                break
            pos += _signed(r)
            if r.get("order_id") is not None and r.get("transaction_type") == "Trade":
                orders.append(r["order_id"])
            if abs(pos) < 1e-9:
                orders = []
        return orders

    def closers_after(symbol: str, after: str) -> list[Any]:
        return [
            r["order_id"] for r in by_sym.get(symbol, []) if str(r.get("executed_at")) > after and _closes(r)
        ]

    def closure(symbol: str) -> set:
        queue, seen = openers_before(symbol, None), set()
        while queue:
            order = queue.pop()
            if order in seen:
                continue
            seen.add(order)
            for r in by_order[order]:
                when = str(r.get("executed_at"))
                queue += openers_before(_sym(r), when) if _closes(r) else closers_after(_sym(r), when)
        return seen

    held = {" ".join(str(leg.get("symbol") or "").split()): _f(leg.get("quantity")) for leg in legs}
    groups: list[tuple[set, set]] = []  # (orders, open symbols)
    for symbol in held:
        orders = closure(symbol)
        if not orders:
            continue
        merged = [g for g in groups if g[0] & orders]
        for g in merged:
            groups.remove(g)
            orders |= g[0]
        groups.append((orders, {symbol}.union(*(g[1] for g in merged))))

    chains = []
    explained: set[str] = set()
    for orders, symbols in groups:
        ordered = sorted(orders, key=lambda o: (str(by_order[o][0].get("executed_at")), str(o)))
        start = str(by_order[ordered[0]][0].get("executed_at"))
        # Replay: the chain's own orders, plus deliveries on its symbols since it began.
        replay: dict[str, float] = defaultdict(float)
        for o in ordered:
            for r in by_order[o]:
                replay[_sym(r)] += _signed(r)
        for r in deliveries:
            if _sym(r) in replay and str(r.get("executed_at")) >= start:
                replay[_sym(r)] += _signed(r)
        reconciled = all(abs(replay.get(s, 0.0) - held[s]) < 1e-9 for s in symbols) and all(
            abs(q) < 1e-9 for s, q in replay.items() if s not in symbols
        )
        rows = _order_rows(ordered, by_order)
        chains.append(
            {
                "symbols": sorted(symbols),
                "orders": rows,
                "cash": sum(r["net"] for r in rows),
                "fees": sum(r["fees"] for r in rows),
                "reconciled": reconciled,
            }
        )
        if reconciled:
            explained |= symbols
    return {"chains": chains, "unchained": sorted(s for s in held if s not in explained)}
