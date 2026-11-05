"""The payoff of each name held: P&L across the underlying's price, at expiry and on dates before it.

For every name the evaluation holds (its trades' legs together, the live loops' 0DTE left out as the
evaluation leaves them out):

- **implied vol** per option leg, solved from its mid with the name's dividend yield: Bjerksund-
  Stensland 2002 (`core.american`, early exercise) for an equity or ETF option, Black-Scholes
  (`core.bs`) for a cash-settled index root, which is European. A leg no vol prices has none, and
  every dated curve that needs it is null rather than drawn on a guess.
- **the dividend yield**: the dividends gone ex in the year to the session over today's price, from
  the snapshot's history-store read (`underlying_history`); `yield_source` says when there was no
  record, and the yield is then 0, stated.
- **curves**: P&L (the legs' model value less their cost, whole position, signed) across a grid of
  prices, on today, 7 and 14 days on (each only before the nearest expiry), and at the nearest
  expiry, where the legs expiring then are worth their intrinsic value and later ones the model's.
  Today's curve passes through today's P&L at today's price, since every vol was solved from its mid.
- **breakevens**: where the nearest-expiry curve crosses zero.

Every number here is a model's, from the snapshot's marks. It is a picture of the position, not a
verdict: it fires no rule.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from cherrypick.core import american, bs

from cherrypick.manage import pmcc as _pmcc

MULTIPLIER = 100.0
POINTS = 49
DATED = (0, 7, 14)


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x


def _unit(leg: dict[str, Any], s: float, years: float, rate: float, q: float, european: bool) -> float | None:
    """One option's model value at price `s` with `years` left; intrinsic at or past expiry."""
    k, right = float(leg["strike"]), leg["right"]
    if years <= 0:
        return bs.intrinsic(s, k, right)
    vol = leg.get("_iv")
    if not vol:
        return None
    if european:
        return bs.price(s, k, years, vol, right, rate, q)
    return american.price(s, k, years, vol, right, rate, q)


def _breakevens(grid: list[float], pnl: list[float]) -> list[float]:
    out = []
    for i in range(1, len(grid)):
        a, b = pnl[i - 1], pnl[i]
        if a == 0:
            out.append(grid[i - 1])
        elif (a < 0) != (b < 0) and b != 0:
            out.append(grid[i - 1] + (grid[i] - grid[i - 1]) * (-a) / (b - a))
    return [round(x, 2) for x in out]


def _name(
    name: str,
    legs: list[dict[str, Any]],
    spot: float | None,
    hist: dict[str, Any] | None,
    session: date,
    rate: float,
    european: bool,
) -> dict[str, Any]:
    out: dict[str, Any] = {"underlying": name, "spot": spot, "model": "european" if european else "american"}
    if spot is None or spot <= 0:
        return {**out, "why": "no price for the underlying"}
    q = _pmcc._yield(hist, session.isoformat(), spot)
    out["div_yield"] = q
    out["yield_source"] = "the last year's dividends" if q is not None else "no dividend record: none assumed"
    q = q or 0.0
    options = [
        leg for leg in legs if leg.get("right") in ("C", "P") and leg.get("strike") and leg.get("expiration")
    ]
    if not options:
        return {**out, "why": "no option leg"}
    costs = [_f(leg.get("cost")) for leg in legs]
    if any(c is None for c in costs):
        return {**out, "why": "a leg has no cost"}
    cost = sum(c for c in costs if c is not None)
    rows = []
    for leg in options:
        mid, years = _f(leg.get("mid")), _f(leg.get("years")) or 0.0
        k = float(leg["strike"])
        iv = None
        if mid is not None and years > 0:
            solve = bs.implied_vol if european else american.implied_vol
            iv = solve(spot, k, years, mid, leg["right"], rate, q)
        leg["_iv"] = iv
        rows.append(
            {
                "leg": leg.get("label") or f"{leg['expiration']} {k:g}{leg['right']}",
                "quantity": _f(leg.get("quantity")),
                "mid": mid,
                "iv": None if iv is None else round(iv, 4),
            }
        )
    days_left = [(date.fromisoformat(str(leg["expiration"])[:10]) - session).days for leg in options]
    nearest = max(0, min(days_left))
    strikes = [float(leg["strike"]) for leg in options] + [spot]
    lo, hi = min(strikes) * 0.8, max(strikes) * 1.2
    grid = [lo + (hi - lo) * i / (POINTS - 1) for i in range(POINTS)]

    def curve(ahead: int) -> list[float] | None:
        pnl = []
        for s in grid:
            total = 0.0
            for leg in legs:
                qty = _f(leg.get("quantity")) or 0.0
                if leg.get("right") not in ("C", "P"):
                    total += qty * s  # shares
                    continue
                left = (date.fromisoformat(str(leg["expiration"])[:10]) - session).days - ahead
                unit = _unit(leg, s, max(left, 0) / 365.0, rate, q, european)
                if unit is None:
                    return None
                total += qty * MULTIPLIER * unit
            pnl.append(round(total - cost, 2))
        return pnl

    curves = []
    for ahead in sorted({d for d in DATED if d < nearest} | {nearest}):
        day = session + timedelta(days=ahead)
        label = (
            "today"
            if ahead == 0
            else (f"at expiry {day.isoformat()}" if ahead == nearest else day.isoformat())
        )
        curves.append(
            {
                "label": label,
                "date": day.isoformat(),
                "days": ahead,
                "expiry": ahead == nearest,
                "pnl": curve(ahead),
            }
        )
    at_expiry = curves[-1]["pnl"]
    for leg in options:
        leg.pop("_iv", None)
    opl = [_f(leg.get("open_pl")) for leg in legs]
    return {
        **out,
        "why": None,
        "rate": rate,
        "cost": round(cost, 2),
        "pnl_now": None if any(v is None for v in opl) else round(sum(v for v in opl if v is not None), 2),
        "grid": [round(s, 4) for s in grid],
        "curves": curves,
        "breakevens": [] if at_expiry is None else _breakevens(grid, at_expiry),
        "legs": rows,
    }


def reading(
    trades: list[dict[str, Any]],
    metrics: dict[str, Any] | None,
    snapshot: dict[str, Any],
    session: date,
    rules: dict[str, Any],
) -> dict[str, Any]:
    """Each held name's payoff, ordered by name."""
    rate = float(rules["pricing"]["rate"])
    cash = {str(s).upper() for s in ((rules.get("expiry") or {}).get("cash_settled_roots") or [])}
    hist = snapshot.get("underlying_history") or {}
    metrics = metrics or {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        name = str(t.get("underlying") or "").upper()
        by_name.setdefault(name, []).extend(dict(leg) for leg in t.get("legs") or [])
    names = []
    for name in sorted(by_name):
        spot = _f((metrics.get(name) or {}).get("last"))
        names.append(_name(name, by_name[name], spot, hist.get(name), session, rate, name in cash))
    return {"names": names}
