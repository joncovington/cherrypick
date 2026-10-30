"""The premium pace: theta carried against its target, premium collected, and premium kept.

A pure reading of the snapshot's account-wide trade history (`account_transactions`, snapshot v15)
and the book's own theta. Three figures, side by side, because each answers a different question:

- **theta against its target** -- `portfolio.theta.target_pct_nlv` of net liq a day, on the theta
  band's own figure (without the legs `front_dte` days or less from expiry). The target sits inside
  the band; it is a pace to hold, not a quantity to maximise (theta is paid for by gamma).
- **collected** -- the net credit of every opening order that took in a credit, in the month it
  filled. What was sold, before anything happened to it.
- **kept** -- the net cash of every option leg that went from flat back to flat, fees included, in
  the month it closed (bought back, expired, exercised, assigned or cash settled). What was actually
  earned. An assigned option's premium counts here; the shares it delivered do not.

The gap between collected and kept is the reading's point: a book that keeps far less than it
collects month after month is paying for its premium in losses.

Two exclusions, each stated with its count and net rather than dropped silently: 0DTE legs on the
ruleset's `exclude.zero_dte_underlyings` (the live loops' own books, judged on their own pages), and
legs whose opening fill is older than the history (their cash is incomplete). Only option legs count.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MONTHS = 6

# A row with no quantity of its own: the cash leg of a cash-settled exercise or assignment, whose
# quantity row (value 0) closes the position beside it.
CASH_ONLY = {"Cash Settled Exercise", "Cash Settled Assignment"}


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x


def _et_day(stamp: Any) -> date | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(ET).date()
    except ValueError:
        return None


def _occ_expiry(symbol: str) -> date | None:
    """`SPXW  261016P07700000` -> 2026-10-16: the six digits before the put/call letter."""
    s = symbol.replace(" ", "")
    for i in range(len(s) - 9, 0, -1):
        if s[i] in "CP" and s[i - 6 : i].isdigit():
            try:
                return datetime.strptime(s[i - 6 : i], "%y%m%d").date()
            except ValueError:
                return None
    return None


def _signed_qty(row: dict[str, Any]) -> float:
    """Contracts the row adds (+, long) or removes (-) on its option symbol; 0 for a cash-only row."""
    sub = row.get("transaction_sub_type")
    if sub in CASH_ONLY:
        return 0.0
    q = abs(_f(row.get("quantity")) or 0.0)
    action = str(row.get("action") or "")
    if action.startswith("Buy"):
        return q
    if action.startswith("Sell"):
        return -q
    if sub == "Exercise":  # a long option exercised: it leaves the account
        return -q
    if sub == "Assignment":  # a short option assigned: it leaves the account
        return q
    return 0.0


def _closing(row: dict[str, Any]) -> bool:
    action = str(row.get("action") or "")
    sub = row.get("transaction_sub_type")
    return action.endswith("to Close") or sub in {"Exercise", "Assignment", "Expiration"}


def _is_option(row: dict[str, Any]) -> bool:
    return "Option" in str(row.get("instrument_type") or "")


def _month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _months_back(session: date, n: int) -> list[str]:
    out, y, m = [], session.year, session.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out[::-1]


def closed_lots(rows: list[dict[str, Any]], zero_dte: set[str]) -> dict[str, Any]:
    """Every option leg's flat-to-flat spans: `lots` (closed on a known opening), `unmatched` (the
    history begins mid-position) and `zero_dte` (excluded), each lot's net cash after fees."""
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if _is_option(r) and r.get("symbol") and _et_day(r.get("executed_at")) is not None:
            by_symbol[str(r["symbol"])].append(r)
    lots: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for symbol, legs in by_symbol.items():
        # Quantity rows before a cash-only row at the same instant, so the cash finds its lot.
        legs.sort(key=lambda r: (str(r.get("executed_at")), r.get("transaction_sub_type") in CASH_ONLY))
        underlying = str(legs[0].get("underlying_symbol") or "").upper()
        expiry = _occ_expiry(symbol)
        qty, cash, opened, partial, last = 0.0, 0.0, None, False, None
        for r in legs:
            day = _et_day(r.get("executed_at"))
            net = _f(r.get("net_value")) or 0.0
            dq = _signed_qty(r)
            if dq == 0.0 and r.get("transaction_sub_type") in CASH_ONLY:
                if qty == 0.0 and last is not None:
                    last["net"] += net  # the settlement cash of the lot its quantity row just closed
                else:
                    cash += net
                continue
            if qty == 0.0:
                opened, cash, partial = day, 0.0, _closing(r)
            qty += dq
            cash += net
            if abs(qty) < 1e-9:
                qty = 0.0
                lot = {
                    "symbol": symbol,
                    "underlying": underlying,
                    "opened": opened,
                    "closed": day,
                    "net": cash,
                }
                if partial:
                    unmatched.append(lot)
                elif underlying in zero_dte and expiry is not None and opened == expiry:
                    excluded.append(lot)
                else:
                    lots.append(lot)
                last = lot
        # A span the history entered by a close and never saw flat again: its opening fill is older
        # than the history, so its cash is incomplete. One entered by an open is simply still held.
        if qty != 0.0 and partial:
            day = _et_day(legs[-1].get("executed_at"))
            unmatched.append(
                {"symbol": symbol, "underlying": underlying, "opened": opened, "closed": day, "net": cash}
            )
    return {"lots": lots, "unmatched": unmatched, "zero_dte": excluded}


def collected_orders(rows: list[dict[str, Any]], zero_dte: set[str]) -> list[dict[str, Any]]:
    """Each order that opened something, at its whole net (before fees) and its day, for orders that
    took in a credit. A roll is one order that closes and opens, so its closing legs count against
    its opening ones: what it collected is the roll's net credit, not the new short's price. An order
    whose legs are all 0DTE on an excluded underlying is left out."""
    orders: dict[Any, dict[str, Any]] = {}
    for r in rows:
        if not (_is_option(r) and r.get("transaction_type") == "Trade"):
            continue
        day = _et_day(r.get("executed_at"))
        if day is None:
            continue
        key = r.get("order_id") if r.get("order_id") is not None else ("row", r.get("id"))
        o = orders.setdefault(key, {"day": day, "value": 0.0, "opens": False, "zero_dte": True})
        o["value"] += _f(r.get("value")) or 0.0
        o["day"] = min(o["day"], day)
        o["opens"] = o["opens"] or str(r.get("action") or "").endswith("to Open")
        expiry = _occ_expiry(str(r.get("symbol") or ""))
        if not (str(r.get("underlying_symbol") or "").upper() in zero_dte and expiry == day):
            o["zero_dte"] = False
    return [o for o in orders.values() if o["opens"] and o["value"] > 0 and not o["zero_dte"]]


def reading(
    rows: list[dict[str, Any]] | None,
    error: str | None,
    session: date,
    book: dict[str, Any],
    net_liq: float | None,
    rules: dict[str, Any],
) -> dict[str, Any]:
    theta_rules = ((rules.get("portfolio") or {}).get("theta")) or {}
    pct = _f(theta_rules.get("target_pct_nlv"))
    theta = _f(book.get("theta_ex_front"))
    if theta is None:
        theta = _f(book.get("theta"))
    target_dollars = pct * net_liq if pct is not None and net_liq else None
    target = {
        "pct_nlv": pct,
        "dollars": round(target_dollars, 2) if target_dollars is not None else None,
        "theta": theta,
        "ratio": round(theta / target_dollars, 3) if theta is not None and target_dollars else None,
    }
    if rows is None or error:
        missing = error or "no account-wide history in this snapshot"
        return {"target": target, "error": missing, "months": [], "week": None}
    zero_dte = {str(s).upper() for s in ((rules.get("exclude") or {}).get("zero_dte_underlyings") or [])}
    spans = closed_lots(rows, zero_dte)
    orders = collected_orders(rows, zero_dte)
    months = _months_back(session, MONTHS)
    by_month = {m: {"month": m, "collected": 0.0, "orders": 0, "kept": 0.0, "closed": 0} for m in months}
    for o in orders:
        m = by_month.get(_month(o["day"]))
        if m is not None:
            m["collected"] += o["value"]
            m["orders"] += 1
    for lot in spans["lots"]:
        m = by_month.get(_month(lot["closed"]))
        if m is not None:
            m["kept"] += lot["net"]
            m["closed"] += 1
    monday = session - timedelta(days=session.weekday())
    week = {
        "from": monday.isoformat(),
        "collected": round(sum(o["value"] for o in orders if monday <= o["day"] <= session), 2),
        "orders": sum(1 for o in orders if monday <= o["day"] <= session),
        "kept": round(sum(lot["net"] for lot in spans["lots"] if monday <= lot["closed"] <= session), 2),
        "closed": sum(1 for lot in spans["lots"] if monday <= lot["closed"] <= session),
    }
    first = min((d for d in (_et_day(r.get("executed_at")) for r in rows) if d is not None), default=None)

    def _summary(lots: list[dict[str, Any]]) -> dict[str, Any]:
        recent = [lot for lot in lots if _month(lot["closed"]) in by_month]
        return {"legs": len(recent), "net": round(sum(lot["net"] for lot in recent), 2)}

    return {
        "target": target,
        "error": None,
        "history_from": first.isoformat() if first else None,
        "months": [
            {**m, "collected": round(m["collected"], 2), "kept": round(m["kept"], 2)}
            for m in by_month.values()
        ],
        "week": week,
        # Left out of `kept`, each with the legs and net it would have added over the months shown.
        "unmatched": _summary(spans["unmatched"]),
        "zero_dte": {**_summary(spans["zero_dte"]), "underlyings": sorted(zero_dte)},
    }
