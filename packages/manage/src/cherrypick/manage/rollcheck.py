"""Check a proposed roll against the ruleset BEFORE it is sent.

A credit roll is not automatically a good roll: it can buy its credit by taking on risk. GLD's 10/09
roll took in $390, and in doing so moved its shorts into the long's own expiry, turned a diagonal into a
broken-wing butterfly and raised what the trade could lose from about $1,100 to $1,693. Each of those is
checked here, from the trade as the evaluation sees it and the order a person means to send:

- credit:     the roll must take in a net credit (the ruleset never rolls for a debit);
- shape:      the trade's shape before and after; a change is named;
- expiry:     shorts that move into the expiry of the long they were sold against end the diagonal;
- coverage:   strikes where the trade is net short more contracts than it owns, before and after;
- breakeven:  every short stays beyond the long's breakeven, counting the roll's own credit;
- risk:       modelled max loss before and after, and against the per-trade budget;
- earnings:   a report inside the new shorts' life (a hard stop for a naked put, a flag otherwise).

Pure: the evaluated trade and the order in, a verdict out ("reject", "risky" or "ok") with its reasons.
Opened legs are priced at the price given (their IV is solved from it); closed legs at the price given
or the trade's mid.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from cherrypick.manage import evaluate as _ev
from cherrypick.manage import pricing
from cherrypick.manage import shapes as _shapes

LEG = re.compile(
    r"^\s*([+-]?\d+(?:\.\d+)?)\s+(\d{4}-\d{2}-\d{2})\s+(\d+(?:\.\d+)?)([CPcp])(?:\s*@\s*~?(\d+(?:\.\d+)?))?\s*$"
)


def parse_leg(text: str) -> dict[str, Any]:
    """'-2 2026-11-20 415C @ 2.57' -> signed quantity, expiration, strike, right, price (or None)."""
    m = LEG.match(text)
    if not m:
        raise ValueError(f"cannot read leg {text!r}; expected like '-2 2026-11-20 415C @ 2.57'")
    qty, exp, strike, right, price = m.groups()
    return {
        "quantity": float(qty),
        "expiration": exp,
        "strike": float(strike),
        "right": right.upper(),
        "price": float(price) if price is not None else None,
    }


def _key(leg: dict[str, Any]) -> tuple:
    return (leg["expiration"], float(leg["strike"]), leg["right"])


def _net_short_runs(legs: list[dict[str, Any]], shares: float = 0.0) -> list[str]:
    """Strike ranges where the trade is short more contracts of a right than it owns (any expiry).
    Shares held cover calls, 100 to a contract, at every strike."""
    out = []
    for right in ("C", "P"):
        by_strike: dict[float, float] = {}
        for leg in legs:
            if leg["right"] == right and leg["quantity"]:
                by_strike[leg["strike"]] = by_strike.get(leg["strike"], 0.0) + leg["quantity"]
        strikes = sorted(by_strike) if right == "C" else sorted(by_strike, reverse=True)
        running, start = (shares / pricing.MULTIPLIER if right == "C" else 0.0), None
        for k in strikes:
            running += by_strike[k]
            if running < 0 and start is None:
                start = k
            elif running >= 0 and start is not None:
                out.append(f"net short {right} from {start:g} to {k:g}")
                start = None
        if start is not None:
            out.append(f"net short {right} from {start:g}, uncovered")
    return out


def _max_loss(
    legs: list[dict[str, Any]], spot: float, cost: float, session: date, pr: dict, shares: float = 0.0
) -> dict:
    expiries = sorted(leg["expiration"] for leg in legs if leg["quantity"])
    horizon = max((date.fromisoformat(expiries[0]) - session).days, 0) / 365.0
    held = [leg for leg in legs if leg["quantity"]]
    if shares:
        # The shares are valued from today's price, so `cost` (the options' basis) still measures the
        # change: a covered call's loss is bounded by the shares falling to the grid's floor.
        held.append({"quantity": shares, "right": None, "years": 0.0})
        cost += shares * spot
    prof = pricing.payoff_profile(held, spot, horizon, pr)
    return {
        "max_loss": -(prof["min_value"] - cost),
        "unbounded": prof["unbounded_low"] or prof["unbounded_high"],
    }


def check(
    trade: dict[str, Any],
    closes: list[dict[str, Any]],
    opens: list[dict[str, Any]],
    *,
    session: date,
    spot: float,
    rules: dict[str, Any],
    net_liq: float | None = None,
    earnings_date: str | None = None,
    shares: float = 0.0,
) -> dict[str, Any]:
    pr = rules["pricing"]
    rate = float(pr["rate"])
    legs = {_key(leg): dict(leg) for leg in trade["legs"] if leg.get("right")}
    problems: list[str] = []
    notes: list[str] = []

    # ---- the order's cash, and the position it leaves
    cash = 0.0
    for c in closes:
        held = legs.get(_key(c))
        if (
            held is None
            or (held["quantity"] > 0) != (c["quantity"] > 0)
            or abs(c["quantity"]) > abs(held["quantity"])
        ):
            raise ValueError(
                f"the trade holds no {c['quantity']:+g} {c['expiration']} {c['strike']:g}{c['right']}"
            )
        price = c["price"] if c["price"] is not None else held["mid"]
        cash += c["quantity"] * price * pricing.MULTIPLIER  # sell a long: +; buy back a short: -
        held["quantity"] -= c["quantity"]
    for o in opens:
        if o["price"] is None:
            raise ValueError(
                f"an opened leg needs a price: {o['quantity']:+g} {o['expiration']} "
                f"{o['strike']:g}{o['right']}"
            )
        cash -= o["quantity"] * o["price"] * pricing.MULTIPLIER  # sell to open: +; buy to open: -
        years = max((date.fromisoformat(o["expiration"]) - session).days, 0) / 365.0
        new = {
            "quantity": o["quantity"],
            "expiration": o["expiration"],
            "strike": o["strike"],
            "right": o["right"],
            "mid": o["price"],
            "years": years,
            "iv": pricing.implied_vol(spot, o["strike"], years, o["price"], o["right"], rate),
        }
        prior = legs.get(_key(new))
        if prior:
            prior["quantity"] += new["quantity"]
        else:
            legs[_key(new)] = new
    after = [leg for leg in legs.values() if abs(leg["quantity"]) > 1e-9]
    before = [leg for leg in trade["legs"] if leg.get("right")]
    cost_before = trade["cost"]
    cost_after = cost_before - cash

    if cash < 0:
        problems.append(f"the roll costs a ${-cash:,.0f} debit; the ruleset only rolls for a net credit")

    # ---- shape
    shape_before = trade.get("shape") or _shapes.classify(before, cost_before)
    shape_after = _shapes.classify(after, cost_after)
    if shape_after != shape_before:
        problems.append(f"the shape changes from {shape_before} to {shape_after}")

    # ---- shorts moving into the long's expiry
    def front_shorts_before_longs(ls: list[dict[str, Any]]) -> bool:
        shorts = [leg for leg in ls if leg["quantity"] < 0]
        longs = [leg for leg in ls if leg["quantity"] > 0]
        return bool(
            shorts and longs and max(s["expiration"] for s in shorts) < max(x["expiration"] for x in longs)
        )

    if front_shorts_before_longs(before) and not front_shorts_before_longs(after):
        problems.append(
            "the shorts move into the long's own expiry: the long no longer outlives them, so the diagonal "
            "becomes a same-expiry position with no time left on the long to absorb a move"
        )

    # ---- coverage
    runs_before, runs_after = _net_short_runs(before, shares), _net_short_runs(after, shares)
    naked_puts_before = any("uncovered" in r and " P " in r for r in runs_before)
    for run in runs_after:
        if "uncovered" not in run:
            continue
        if " C " in run:
            problems.append(f"{run}: the loss is unlimited")
        elif not naked_puts_before:
            problems.append(f"{run}: the roll leaves naked short puts, at risk down to zero")
    if runs_after and runs_after != runs_before:
        notes.append("net short ranges: " + ("; ".join(runs_after) or "none"))

    # ---- breakeven, counting the roll's credit
    longs = [leg for leg in after if leg["quantity"] > 0]
    edges = _ev.long_breakeven(longs, cost_after)
    for short in (leg for leg in after if leg["quantity"] < 0):
        edge = edges.get(short["right"])
        if edge is None:
            continue
        if (short["strike"] < edge) if short["right"] == "C" else (short["strike"] > edge):
            problems.append(
                f"the short {short['strike']:g}{short['right']} sits inside the long's breakeven of "
                f"{edge:.2f}: "
                "an assignment there would lock in a loss"
            )

    # ---- risk before and after
    risk_before = _max_loss(before, spot, cost_before, session, pr, shares)
    risk_after = _max_loss(after, spot, cost_after, session, pr, shares)
    if risk_after["unbounded"]:
        problems.append("after the roll the modelled loss is unbounded")
    elif risk_after["max_loss"] > risk_before["max_loss"] * 1.10 + 1:
        problems.append(
            f"what the trade can lose grows from ${risk_before['max_loss']:,.0f} "
            f"to ${risk_after['max_loss']:,.0f}"
        )
    # Shares behind a covered call carry their own risk, the holding's and not the roll's, so the budget
    # is judged on option-only trades.
    if net_liq and not shares and not risk_after["unbounded"]:
        budget = float(rules["risk"]["max_risk_pct"]) * net_liq
        if risk_after["max_loss"] > budget * float(rules["risk"]["oversize_multiple"]):
            problems.append(
                f"${risk_after['max_loss']:,.0f} at risk is {risk_after['max_loss'] / budget:.1f}x the "
                f"${budget:,.0f} per-trade budget"
            )

    # ---- earnings inside the new shorts' life
    if earnings_date:
        short_exps = [leg["expiration"] for leg in after if leg["quantity"] < 0]
        if short_exps and session.isoformat() <= earnings_date <= max(short_exps):
            if shape_after == "naked put":
                problems.append(f"it carries a naked put through earnings on {earnings_date}: close instead")
            else:
                notes.append(f"earnings on {earnings_date} fall inside the new shorts' life")

    reject = cash < 0 or any("unbounded" in p or "unlimited" in p or "earnings" in p for p in problems)
    verdict = "reject" if reject else "risky" if problems else "ok"
    return {
        "verdict": verdict,
        "credit": cash,
        "basis_before": cost_before,
        "basis_after": cost_after,
        "shape_before": shape_before,
        "shape_after": shape_after,
        "max_loss_before": None if risk_before["unbounded"] else risk_before["max_loss"],
        "max_loss_after": None if risk_after["unbounded"] else risk_after["max_loss"],
        "problems": problems,
        "notes": notes,
        "after": [
            f"{leg['quantity']:+g} {leg['expiration']} {leg['strike']:g}{leg['right']}"
            for leg in sorted(after, key=lambda x: (x["expiration"], x["right"], x["strike"]))
        ],
    }
