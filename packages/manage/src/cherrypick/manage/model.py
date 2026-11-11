"""Model a trade: a proposed set of legs drawn on a name's payoff with what it already holds, and the
caps it would use (2026-11-04). Advice only: it prices a hypothesis, sends nothing, sizes nothing.

Each proposed leg is `rollcheck.parse_leg` text ("-1 2026-12-18 46P @ 1.05"). A leg given a price is
taken at it and its vol solved from it, as every held leg's is; a leg with none is priced at the
name's IV index (the broker's), and says so. The payoff is `payoff.py`'s, before and after.

For the proposed legs alone: their net credit (+) or debit, delta and theta a day (the model's,
whole position), and their worst case at their own nearest expiry over a wide price range, which is
the buying power a defined-risk trade holds in a cash account; a worst case still falling at the
range's edge is unbounded (a naked call), stated, never a number.

Against the ruleset's candidate caps (`portfolio.candidates`): the worst case as a share of net liq
(`per_trade_risk_pct`), the name's worst case with it (`notional_per_name_pct`, by the account's
kind), and buying power in use with it (the cash account's total cap, else `max_buying_power_pct`).
And the name's implied vol against its 30-day realised, shown beside each other: the one edge a
short option has, never ranked on.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from cherrypick.core import american, bs

from cherrypick.manage import candidates as _candidates
from cherrypick.manage import payoff as _payoff
from cherrypick.manage import pmcc as _pmcc
from cherrypick.manage import rollcheck as _rollcheck

MULTIPLIER = 100.0
WIDE = 121


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x


def _price(
    european: bool, s: float, k: float, years: float, vol: float, right: str, rate: float, q: float
) -> float:
    fn = bs.price if european else american.price
    return fn(s, k, years, vol, right, rate, q)


def _leg(raw: dict[str, Any], session: date) -> dict[str, Any]:
    days = (date.fromisoformat(raw["expiration"]) - session).days
    return {
        "right": raw["right"],
        "strike": raw["strike"],
        "expiration": raw["expiration"],
        "quantity": raw["quantity"],
        "years": max(days, 0) / 365.0,
        "label": f"{raw['quantity']:+g} {raw['expiration']} {raw['strike']:g}{raw['right']}",
    }


def model(
    evaluation: dict[str, Any],
    snapshot: dict[str, Any],
    rules: dict[str, Any],
    underlying: str,
    proposed: list[str],
) -> dict[str, Any]:
    name = underlying.upper()
    session = date.fromisoformat(str(evaluation["session"]))
    rate = float(rules["pricing"]["rate"])
    european = name in {str(s).upper() for s in ((rules.get("expiry") or {}).get("cash_settled_roots") or [])}
    accounts = evaluation.get("accounts") or []
    account = next((a for a in accounts if a.get("designated")), accounts[0] if accounts else None)
    if account is None:
        return {"ok": False, "error": "the evaluation holds no account"}
    metrics = (snapshot.get("metrics") or {}).get(name) or {}
    spot = _f(metrics.get("last"))
    if spot is None or spot <= 0:
        return {"ok": False, "error": f"no price for {name} in the snapshot"}
    try:
        parsed = [_rollcheck.parse_leg(t) for t in proposed]
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not parsed:
        return {"ok": False, "error": "no leg to model"}
    for p in parsed:
        if p["expiration"] < session.isoformat():
            return {"ok": False, "error": f"{p['expiration']} has already expired"}
    hist = (snapshot.get("underlying_history") or {}).get(name)
    q = _pmcc._yield(hist, session.isoformat(), spot) or 0.0
    iv_index = _f(metrics.get("iv_index"))
    hv = _f(metrics.get("hv_30_day_pct"))

    new = []
    for p in parsed:
        leg = _leg(p, session)
        if p["price"] is not None:
            price, source = p["price"], "the price given"
        elif iv_index:
            price = _price(european, spot, p["strike"], leg["years"], iv_index, p["right"], rate, q)
            source = f"the name's IV index ({iv_index:.1%})"
        else:
            return {"ok": False, "error": f"no price given for {leg['label']} and no IV index for {name}"}
        leg.update(
            {
                "mid": price,
                "cost": leg["quantity"] * MULTIPLIER * price,
                "value": leg["quantity"] * MULTIPLIER * price,
                "open_pl": 0.0,
                "price_source": source,
            }
        )
        new.append(leg)

    held = [
        dict(leg)
        for t in account.get("trades") or []
        if str(t.get("underlying") or "").upper() == name
        for leg in t.get("legs") or []
    ]
    before = (
        _payoff._name(name, [dict(x) for x in held], spot, hist, session, rate, european) if held else None
    )
    after = _payoff._name(name, [dict(x) for x in held + new], spot, hist, session, rate, european)

    # The proposed legs alone: each one's model greeks at its solved vol, and their worst case.
    rows, delta, theta = [], 0.0, 0.0
    for leg in new:
        solve = bs.implied_vol if european else american.implied_vol
        iv = (
            solve(spot, leg["strike"], leg["years"], leg["mid"], leg["right"], rate, q)
            if leg["years"] > 0
            else None
        )
        d = th = None
        if iv:
            h = spot * 0.005
            up = _price(european, spot + h, leg["strike"], leg["years"], iv, leg["right"], rate, q)
            dn = _price(european, spot - h, leg["strike"], leg["years"], iv, leg["right"], rate, q)
            d = (up - dn) / (2 * h)
            later = max(leg["years"] - 1 / 365.0, 0.0)
            th = _price(european, spot, leg["strike"], later, iv, leg["right"], rate, q) - leg["mid"]
            delta += d * leg["quantity"] * MULTIPLIER
            theta += th * leg["quantity"] * MULTIPLIER
        rows.append(
            {
                "leg": leg["label"],
                "price": round(leg["mid"], 4),
                "price_source": leg["price_source"],
                "iv": None if iv is None else round(iv, 4),
                "delta": None if d is None else round(d, 4),
                "theta_day": None if th is None else round(th * leg["quantity"] * MULTIPLIER, 2),
            }
        )
    all_greeks = all(r["iv"] is not None for r in rows)
    net = -sum(leg["cost"] for leg in new)
    first = min(leg["years"] for leg in new)
    wide = [spot * 3.0 * i / (WIDE - 1) for i in range(WIDE)]  # zero to three times today's price
    worst_at, worst = None, None
    edge_falling = False
    values = []
    for s in wide:
        total = net
        for leg in new:
            left = leg["years"] - first
            if left <= 0:
                unit = bs.intrinsic(s, leg["strike"], leg["right"])
            else:
                vol = next((r["iv"] for r in rows if r["leg"] == leg["label"]), None) or iv_index
                if not vol:
                    total = None
                    break
                unit = _price(european, s, leg["strike"], left, vol, leg["right"], rate, q)
            total += leg["quantity"] * MULTIPLIER * unit
        values.append(total)
    if all(v is not None for v in values):
        i = min(range(len(values)), key=values.__getitem__)
        worst, worst_at = values[i], wide[i]
        edge_falling = i == len(values) - 1 and values[-1] < values[-2]
    risk = None if worst is None or edge_falling else max(0.0, -worst)

    net_liq = _f(account.get("net_liq"))
    bal = next(
        (
            x.get("balances") or {}
            for x in ((snapshot.get("positions") or {}).get("accounts") or [])
            if x.get("account") == account.get("account")
        ),
        {},
    )
    used = _f(bal.get("used_derivative_buying_power"))
    raw_account = next(
        (
            x
            for x in ((snapshot.get("positions") or {}).get("accounts") or [])
            if x.get("account") == account.get("account")
        ),
        {},
    )
    basis = _candidates.account_basis(raw_account.get("account_type"))
    cands = (rules.get("portfolio") or {}).get("candidates") or {}
    per_name = cands.get("notional_per_name_pct") or {}
    name_cap = _f(per_name.get("cash" if basis == "cash" else "margin"))
    # A cash account's total cap is the portfolio reading's (`portfolio.cash_bands`), not the
    # candidates' margin-era fallback.
    bp_band = ((account.get("portfolio") or {}).get("bands") or {}).get("buying_power") or {}
    bp_cap = (
        _f(bp_band.get("high")) if bp_band.get("basis") == "cash" else _f(cands.get("max_buying_power_pct"))
    )
    name_before = None
    if before and before.get("curves") and before["curves"][-1]["pnl"]:
        name_before = max(0.0, -min(before["curves"][-1]["pnl"]))

    def share(v: float | None) -> float | None:
        return None if v is None or not net_liq else round(v / net_liq, 4)

    caps = [
        {
            "label": "this trade's worst case / net liq",
            "value": share(risk),
            "cap": _f(cands.get("per_trade_risk_pct")),
        },
        {
            "label": f"{name}'s worst case with it / net liq",
            "value": share(None if risk is None else (name_before or 0.0) + risk),
            "cap": name_cap,
        },
        {
            "label": "buying power in use with it / net liq",
            "value": share(None if risk is None or used is None else used + risk),
            "cap": bp_cap,
        },
    ]
    for c in caps:
        c["over"] = None if c["value"] is None or c["cap"] is None else c["value"] > c["cap"]
    return {
        "ok": True,
        "session": evaluation["session"],
        "underlying": name,
        "spot": spot,
        "model": "european" if european else "american",
        "legs": rows,
        "net": round(net, 2),
        "delta": round(delta, 2) if all_greeks else None,
        "theta_day": round(theta, 2) if all_greeks else None,
        "worst_case": None if risk is None else round(-risk, 2),
        "worst_case_at": None if worst_at is None or edge_falling else round(worst_at, 2),
        "unbounded": edge_falling,
        "account_basis": basis,
        "caps": caps,
        "iv_index": iv_index,
        "hv_30_day": None if hv is None else hv / 100.0,
        "before": before,
        "after": after,
    }
