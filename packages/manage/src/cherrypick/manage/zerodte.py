"""The 0DTE legs the ruleset leaves out, split into the live loops' and the ones placed by hand.

`exclude.zero_dte_underlyings` keeps every leg expiring today on those names out of the evaluation:
they are the live loops' books, judged on the loops' own pages. A trade placed by hand on the same
names and expiry (the broker app, no loop behind it) fell out with them, and no surface showed it
(2026-11-03). This reading puts it back as its own block, judged by nothing: what was filled, what is
still held, and what it is worth at the mark.

A pure reading of the snapshot:

- **Today's fills** are the account-wide history's (`account_transactions`, v15) `Trade` rows on an
  excluded underlying, an option expiring today, executed today (ET).
- **A fill is a loop's** when its order id is one a live loop has published
  (`live_known_orders`, v16: the ids each loop's orphan sweep writes, `core.execution`). That is the
  loops' own belief about which orders are theirs, the same one their sweeps use. Every other fill is
  by hand. A fill newer than the newest publish is counted by hand but listed in `after_publish`: a
  loop publishes its ledger's ids once a tick, so an order placed this minute may not be listed yet.
- **What is held by hand** is each symbol's net hand quantity today. It must reconcile: the snapshot's
  held quantity equals the loops' net fills plus the hand's. A symbol that does not (a leg opened
  before today, a history that does not reach back far enough, a fill the reading cannot see) is
  listed in `unreconciled`, and the mark and the net are null rather than part-counted.

Money follows the suite's one layout: signed cash (a credit `+`), whole-position dollars, trading fees
their own column (the broker's own `value - net_value` per fill), and net = gross - fees - settlement.
While legs are still held the gross includes their mark at mid (`mark`), and the net says so
(`net_basis`). Settlement is null while a held leg has still to settle (the cash settlement is booked
the next morning); with nothing held it is 0, because nothing settled.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from cherrypick.manage.premium import _et_day, _f, _is_option, _occ_expiry, _signed_qty


def _today_fill(row: dict[str, Any], names: set[str], session: date) -> bool:
    if row.get("transaction_type") != "Trade" or not _is_option(row):
        return False
    if str(row.get("underlying_symbol") or "").upper() not in names:
        return False
    return _occ_expiry(str(row.get("symbol") or "")) == session and _et_day(row.get("executed_at")) == session


def _fees(row: dict[str, Any]) -> float | None:
    """The broker's own fee total on one fill, as a positive cost: `value - net_value`."""
    value, net = _f(row.get("value")), _f(row.get("net_value"))
    if value is None or net is None:
        return None
    return round(value - net, 2)


def _order_kind(actions: set[str]) -> str:
    opens = any(a.endswith("to Open") for a in actions)
    closes = any(a.endswith("to Close") for a in actions)
    return "mixed" if opens and closes else "open" if opens else "close" if closes else "unknown"


def _r2(v: float) -> float:
    return round(v + 0.0, 2)


def reading(
    *,
    session: date,
    names: set[str],
    held: list[dict[str, Any]],
    transactions: list[dict[str, Any]] | None,
    transactions_error: str | None,
    known: dict[str, dict[str, Any]] | None,
    known_error: str | None,
) -> dict[str, Any]:
    """The hand 0DTE view for one account. `held` is the legs `_without_excluded` dropped, `names`
    the excluded underlyings, `transactions` the account-wide history (None: the snapshot predates
    it), `known` the loops' published order ids (None: the snapshot predates v16).

    `held` also carries legs that expired before today: the broker lists an expired leg until it
    processes the expiry overnight. Those are an earlier session's, which today's fills can never
    explain, so they are set aside in `expired_held` and left out of the reconciliation. They are
    not a caveat: the morning after every 0DTE session has them, and nothing about today is in doubt."""
    held, expired = _split_expired(held, session)
    caveats: list[str] = []
    if transactions is None or transactions_error:
        return _unread(
            session, names, held, expired, transactions_error or "no account-wide history in this snapshot"
        )
    if known is None or known_error:
        why = known_error or "no loop order ids in this snapshot (taken before v16)"
        return _unread(session, names, held, expired, why)

    known_ids: set[str] = set()
    newest_publish: datetime | None = None
    for module, doc in sorted(known.items()):
        if doc.get("error"):
            caveats.append(f"{module}'s published order ids could not be read: {doc['error']}")
        known_ids.update(str(i) for i in doc.get("order_ids") or [])
        at = _stamp(doc.get("at"))
        if at is not None and (newest_publish is None or at > newest_publish):
            newest_publish = at

    fills = [r for r in transactions if _today_fill(r, names, session)]
    # The history is newest first and capped: if even its oldest row is today's, today's may not all be in it.
    oldest = _et_day(transactions[-1].get("executed_at")) if transactions else None
    if oldest == session:
        caveats.append(
            "the account history holds nothing older than today, so today's fills may not all be in it"
        )

    loop_qty: dict[str, float] = defaultdict(float)
    hand_qty: dict[str, float] = defaultdict(float)
    hand_fills: list[dict[str, Any]] = []
    loop_fills = 0
    for r in fills:
        symbol = str(r.get("symbol") or "")
        oid = r.get("order_id")
        if oid is not None and str(oid) in known_ids:
            loop_qty[symbol] += _signed_qty(r)
            loop_fills += 1
        else:
            hand_qty[symbol] += _signed_qty(r)
            hand_fills.append(r)

    held_by: dict[str, dict[str, Any]] = {}
    held_qty: dict[str, float] = defaultdict(float)
    for leg in held:
        symbol = str(leg.get("symbol") or "")
        held_by[symbol] = leg
        held_qty[symbol] += _f(leg.get("quantity")) or 0.0

    unreconciled = []
    for symbol in sorted({*held_qty, *loop_qty, *hand_qty}):
        expected = loop_qty.get(symbol, 0.0) + hand_qty.get(symbol, 0.0)
        if abs(held_qty.get(symbol, 0.0) - expected) > 1e-9:
            unreconciled.append(
                {
                    "symbol": symbol,
                    "held": held_qty.get(symbol, 0.0),
                    "loop_fills": loop_qty.get(symbol, 0.0),
                    "hand_fills": hand_qty.get(symbol, 0.0),
                }
            )

    orders_by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in hand_fills:
        orders_by[str(r.get("order_id"))].append(r)
    orders = []
    for oid, rows in orders_by.items():
        fees = [_fees(r) for r in rows]
        orders.append(
            {
                "order_id": None if oid == "None" else oid,
                "at": min(str(r.get("executed_at") or "") for r in rows) or None,
                "kind": _order_kind({str(r.get("action") or "") for r in rows}),
                "legs": [
                    {
                        "symbol": r.get("symbol"),
                        "action": r.get("action"),
                        "quantity": _f(r.get("quantity")),
                        "price": _f(r.get("price")),
                        "value": _f(r.get("value")),
                    }
                    for r in rows
                ],
                "cash": _r2(sum(_f(r.get("value")) or 0.0 for r in rows)),
                "fees": None if any(f is None for f in fees) else _r2(sum(f or 0.0 for f in fees)),
            }
        )
    orders.sort(key=lambda o: o["at"] or "")

    open_legs = []
    for symbol, qty in sorted(hand_qty.items()):
        if abs(qty) < 1e-9:
            continue
        leg = held_by.get(symbol) or {}
        mid = _f(leg.get("mid")) if leg.get("priced") else None
        mult = _f(leg.get("multiplier")) or 100.0
        open_legs.append(
            {
                "symbol": symbol,
                "right": leg.get("right"),
                "strike": _f(leg.get("strike")),
                "quantity": qty,
                "mid": mid,
                "value": None if mid is None else _r2(qty * mid * mult),
                "mark_source": leg.get("mark_source"),
                "mark_age_seconds": _f(leg.get("mark_age_seconds")),
            }
        )

    after_publish = [
        o["order_id"]
        for o in orders
        if newest_publish is not None and (_stamp(o["at"]) or newest_publish) > newest_publish
    ]
    if newest_publish is None and orders:
        caveats.append("no live loop has published its order ids, so every fill today counts as by hand")

    cash = _r2(sum(o["cash"] for o in orders))
    fee_parts = [o["fees"] for o in orders]
    fees_total = None if any(f is None for f in fee_parts) else _r2(sum(f or 0.0 for f in fee_parts))
    whole = not unreconciled
    mark = (
        None
        if not whole or any(leg["value"] is None for leg in open_legs)
        else _r2(sum(leg["value"] for leg in open_legs))
    )
    gross = None if mark is None else _r2(cash + mark)
    settlement = None if open_legs else 0.0
    net = None if gross is None or fees_total is None else _r2(gross - fees_total - (settlement or 0.0))
    return {
        "session": session.isoformat(),
        "underlyings": sorted(names),
        "read": True,
        "error": None,
        "orders": orders,
        "open_legs": open_legs,
        "unreconciled": unreconciled,
        "expired_held": expired,
        "after_publish": after_publish,
        "known_modules": {m: doc.get("at") for m, doc in sorted(known.items())},
        "loop_fills": loop_fills,
        "cash": cash,
        "mark": mark,
        "gross": gross,
        "fees": fees_total,
        "settlement": settlement,
        "net": net,
        "net_basis": "at mark: held legs at mid, not yet settled" if open_legs else "realised: nothing held",
        "caveats": caveats,
    }


def _stamp(v: Any) -> datetime | None:
    """A fill's UTC `Z` stamp or a loop's offset stamp as an aware datetime; None when unreadable."""
    if not isinstance(v, str) or not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo is not None else None


def _leg_expiry(leg: dict[str, Any]) -> date | None:
    """A held leg's expiry: its OCC symbol's, else its `expiration` field; None when neither reads."""
    exp = _occ_expiry(str(leg.get("symbol") or ""))
    if exp is not None:
        return exp
    try:
        return date.fromisoformat(str(leg.get("expiration") or "")[:10])
    except ValueError:
        return None


def _split_expired(
    held: list[dict[str, Any]], session: date
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Today's held legs, and those that expired on an earlier day ({symbol, expiry, quantity})."""
    today, expired = [], []
    for leg in held:
        exp = _leg_expiry(leg)
        if exp is not None and exp < session:
            expired.append(
                {
                    "symbol": leg.get("symbol"),
                    "expiry": exp.isoformat(),
                    "quantity": _f(leg.get("quantity")),
                }
            )
        else:
            today.append(leg)
    return today, expired


def _unread(
    session: date,
    names: set[str],
    held: list[dict[str, Any]],
    expired: list[dict[str, Any]],
    why: str,
) -> dict[str, Any]:
    return {
        "session": session.isoformat(),
        "underlyings": sorted(names),
        "read": False,
        "error": why,
        "orders": [],
        "open_legs": [],
        "unreconciled": [],
        "expired_held": expired,
        "after_publish": [],
        "known_modules": {},
        "loop_fills": 0,
        "held_legs": len(held),
        "cash": None,
        "mark": None,
        "gross": None,
        "fees": None,
        "settlement": None,
        "net": None,
        "net_basis": None,
        "caveats": [],
    }
