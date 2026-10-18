"""Income trades from our own universe, sized to the account (docs/portfolio-guidance-plan.md, 4).

A pure function of the evening `income` reading the snapshot carries (`fetch_screener_greeks.py
income`: every tradable name's cycle-monthly chain, quoted in regular hours, with its earnings dates,
beta and stress move) and the portfolio reading manage has already made of the book. For each name it
builds the structures the suite's screener rules describe, keeps the ones whose every leg is quoted,
liquid by the leg bars and clear of an earnings report, and gives each a QUANTITY: the largest that
clears every cap, with the cap that stops it named.

The structures (the vendor's rules as `packages/technicals` `screeners.py` reproduced them, on the
user's cycle monthly rather than the vendor's expiry):

- a **short put** at the strike nearest `short_put_delta` (0.40);
- a **put or call credit spread**: short the first strike out of the money, long the strike nearest
  `spread_long_delta` (0.25) beyond it, offered only at a credit of `spread_min_credit` (a third) of
  the width;
- a **covered call** at the strike nearest `covered_call_delta` (0.165), only on a name the account
  holds at least 100 shares of, never more contracts than the shares cover.

Deltas are Black-Scholes on each strike's own IV, solved from its mid, as the screener rules found.

The caps, each scaled by net liq, so the same rules size a larger account larger:

- **per-trade risk** -- what one unit can lose: a spread's max loss; a short put's STRESS loss, the
  name's own 1st-percentile move over the holding period at expiry, less the credit;
- **notional per name** -- a short put's strike x 100 against a share of net liq that depends on the
  account (a cash account or IRA secures it in full; margin does not, which is why leverage is held
  by notional, not buying power);
- **buying power** -- used after the trade under the VIX-scaled cap the portfolio reading states;
- **delta** -- the book's beta-weighted delta stays inside its band, or moves toward it;
- **theta** -- the book's theta stays under the band's ceiling;
- **concentration** -- the name stays under its share of the book's gross theta.

Buying power per unit follows the account kind the snapshot read: cash or an IRA secures a put at
its strike; Reg T margin takes the greater of 20% of the underlying less the out-of-the-money amount
plus the premium, and 10% of the strike plus the premium; portfolio margin is not modelled, and says
so. An unknown kind is sized as cash-secured, the cautious reading.

Every figure is modelled, at mid. Nothing here places an order, and the evening reading is a day old
by the morning: re-quote before sending.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from cherrypick.manage import portfolio as _portfolio
from cherrypick.manage import pricing

MULTIPLIER = pricing.MULTIPLIER


def _f(value: Any) -> float | None:
    return _portfolio._f(value)


# --------------------------------------------------------------------------- one name's chain
def priced_options(entry: dict[str, Any], session: date, rate: float) -> list[dict[str, Any]]:
    """The name's quoted options with mid, IV solved from it, and model greeks. An option without a
    two-sided quote, or whose mid no vol can price, is left out."""
    spot, dte = _f(entry.get("spot")), entry.get("dte")
    if not spot or not dte or dte <= 0:
        return []
    years = dte / 365.0
    out = []
    for o in entry.get("options") or []:
        bid, ask, k = _f(o.get("bid")), _f(o.get("ask")), _f(o.get("strike"))
        if (
            bid is None
            or ask is None
            or ask < bid
            or ask <= 0
            or k is None
            or o.get("right") not in ("C", "P")
        ):
            continue
        mid = (bid + ask) / 2.0
        if mid <= 0:
            continue
        iv = pricing.implied_vol(spot, k, years, mid, o["right"], rate)
        g = pricing.greeks(spot, k, years, iv, o["right"], rate) if iv else None
        if g is None:
            continue
        out.append({**o, "strike": k, "bid": bid, "ask": ask, "mid": mid, "iv": iv, **g})
    return out


def _nearest(options: list[dict], right: str, target: float, where=lambda o: True) -> dict | None:
    pool = [o for o in options if o["right"] == right and where(o)]
    return min(pool, key=lambda o: abs(abs(o["delta"]) - target)) if pool else None


def structures(entry: dict[str, Any], options: list[dict], cfg: dict[str, Any], shares: float) -> list[dict]:
    """Every structure the rules build on this name, legs as (option, signed quantity per unit)."""
    spot = _f(entry.get("spot")) or 0.0
    out = []
    sp = _nearest(options, "P", float(cfg["short_put_delta"]))
    if sp:
        out.append({"kind": "short_put", "legs": [(sp, -1)]})
    for right in ("P", "C"):
        otm = [
            o
            for o in options
            if o["right"] == right and (o["strike"] <= spot if right == "P" else o["strike"] >= spot)
        ]
        if not otm:
            continue
        short = max(otm, key=lambda o: o["strike"]) if right == "P" else min(otm, key=lambda o: o["strike"])
        k_short = short["strike"]
        beyond = (
            (lambda o, k=k_short: o["strike"] < k) if right == "P" else (lambda o, k=k_short: o["strike"] > k)
        )
        long_ = _nearest(options, right, float(cfg["spread_long_delta"]), beyond)
        if long_:
            out.append(
                {"kind": "put_spread" if right == "P" else "call_spread", "legs": [(short, -1), (long_, 1)]}
            )
    if shares >= 100:
        cc = _nearest(options, "C", float(cfg["covered_call_delta"]), lambda o: o["strike"] > spot)
        if cc:
            out.append({"kind": "covered_call", "legs": [(cc, -1)], "max_units": int(shares // 100)})
    return out


def fitted_spread(
    options: list[dict], short: dict, max_loss_cap: float, min_credit_share: float, was: str
) -> dict | None:
    """The nearest version that fits: a credit spread on `short`'s strike whose long is the WIDEST
    listed strike beyond it that keeps one unit's max loss under `max_loss_cap` and the credit at
    least `min_credit_share` of the width. A short put or a spread the rule built too wide for the
    account becomes this; None when no listed long fits."""
    right = short["right"]
    beyond = [
        o
        for o in options
        if o["right"] == right
        and (o["strike"] < short["strike"] if right == "P" else o["strike"] > short["strike"])
    ]
    best = None
    for o in sorted(beyond, key=lambda o: abs(o["strike"] - short["strike"])):
        width = abs(o["strike"] - short["strike"])
        credit = short["mid"] - o["mid"]
        if credit < min_credit_share * width or (width - credit) * MULTIPLIER > max_loss_cap:
            continue
        best = o
    if best is None:
        return None
    return {
        "kind": "put_spread" if right == "P" else "call_spread",
        "legs": [(short, -1), (best, 1)],
        "alternative_to": was,
    }


# --------------------------------------------------------------------------- one unit
def unit(
    s: dict[str, Any], entry: dict[str, Any], account_kind: str, scale: float | None
) -> dict[str, Any] | None:
    """Per-unit economics of a structure, or None when it fails its own rule (a spread under the
    minimum credit). Signed cash: a credit received is +."""
    legs = s["legs"]
    spot = _f(entry.get("spot")) or 0.0
    credit = -sum(q * o["mid"] for o, q in legs)  # per share
    natural = -sum(q * (o["ask"] if q > 0 else o["bid"]) for o, q in legs)
    stress = entry.get("stress") or {}
    kind = s["kind"]
    if kind in ("put_spread", "call_spread"):
        width = abs(legs[0][0]["strike"] - legs[1][0]["strike"])
        max_loss = (width - credit) * MULTIPLIER
        bp = max_loss
        stress_loss = max_loss  # defined: the worst case is the stress case
        notional = max_loss
    elif kind == "short_put":
        k = legs[0][0]["strike"]
        width = None
        max_loss = (k - credit) * MULTIPLIER
        notional = k * MULTIPLIER
        if account_kind == "margin":
            otm = max(spot - k, 0.0)
            bp = max(0.20 * spot - otm + credit, 0.10 * k + credit) * MULTIPLIER
        elif account_kind == "portfolio_margin":
            bp = None
        else:
            bp = (k - credit) * MULTIPLIER
        p01 = _f(stress.get("p01"))
        stress_loss = (
            None if p01 is None else max(k - spot * (1.0 + p01), 0.0) * MULTIPLIER - credit * MULTIPLIER
        )
    else:  # covered call on held shares: the shares secure it; their risk is already in the book
        width = None
        max_loss = None
        notional = 0.0
        bp = 0.0
        stress_loss = 0.0
    if kind in ("put_spread", "call_spread") and width and credit < s.get("min_credit_share", 0.0) * width:
        return None
    theta = sum(q * MULTIPLIER * o["theta"] for o, q in legs)
    delta = sum(q * MULTIPLIER * o["delta"] for o, q in legs)
    gamma = sum(q * MULTIPLIER * o["gamma"] for o, q in legs)
    return {
        "credit": credit,
        "natural": natural,
        "width": width,
        "max_loss": max_loss,
        "bp": bp,
        "stress_loss": stress_loss,
        "notional": notional,
        "theta": theta,
        "bwd": delta * scale if scale is not None else None,
        "gamma_bench": gamma * scale * scale if scale is not None else None,
    }


# --------------------------------------------------------------------------- sizing
def account_basis(account_type: dict[str, Any] | None) -> str:
    """'cash' (a cash account or IRA, and an unknown one), 'margin' or 'portfolio_margin'."""
    kind = str((account_type or {}).get("margin_or_cash") or "").lower()
    name = str((account_type or {}).get("type_name") or "").lower()
    if "ira" in name or kind in ("cash", ""):
        return "cash"
    if "portfolio" in kind or "portfolio" in name:
        return "portfolio_margin"
    return "margin"


def size(
    u: dict[str, Any],
    book: dict[str, Any],
    underlying: str,
    cfg: dict[str, Any],
    basis: str,
    max_units: int,
) -> tuple[int, str | None]:
    """(units, binding cap): the largest count that clears every cap, and the cap the next unit
    breaks (None when `max_units` itself is what stops it)."""
    net_liq = book.get("net_liq")
    if not net_liq:
        return 0, "no net liq"
    used = book.get("buying_power_used")
    bp_cap = ((book.get("bands") or {}).get("buying_power") or {}).get("high")
    bp_cap = float(cfg["max_buying_power_pct"]) if bp_cap is None else bp_cap
    band = (book.get("bands") or {}).get("delta") or {}
    theta_hi = ((book.get("bands") or {}).get("theta") or {}).get("high")
    theta = book.get("theta") or 0.0
    bwd = book.get("bwd")
    row = next((x for x in book.get("underlyings") or [] if x["underlying"] == underlying), None)
    name_theta = (row or {}).get("theta") or 0.0
    # One name's share of the theta the account may CARRY (the band's ceiling in dollars), not of
    # what it carries now: against the book as it is, the first trade in an empty book is 100% of it.
    name_theta_cap = (
        float(cfg["max_name_theta_share"]) * (theta_hi if theta_hi is not None else 0.003) * net_liq
    )
    notional_pct = float(cfg["notional_per_name_pct"]["margin" if basis != "cash" else "cash"])

    def breaks(n: int) -> str | None:
        if u["stress_loss"] is None:
            return "no stress reading"
        if n * u["stress_loss"] > float(cfg["per_trade_risk_pct"]) * net_liq:
            return "per-trade risk"
        if n * u["notional"] > notional_pct * net_liq:
            return "notional per name"
        if u["bp"] is not None and used is not None and used + n * u["bp"] > bp_cap * net_liq:
            return "buying power"
        if (
            bwd is not None
            and u["bwd"] is not None
            and band.get("low") is not None
            and band.get("high") is not None
        ):
            after = bwd + n * u["bwd"]
            lo, hi = band["low"], band["high"]
            inside_after = lo <= after <= hi
            closer = abs(after - min(max(after, lo), hi)) < abs(bwd - min(max(bwd, lo), hi))
            if not inside_after and not closer:
                return "delta band"
        if theta_hi is not None and (theta + n * u["theta"]) / net_liq > theta_hi:
            return "theta ceiling"
        if n * u["theta"] > 0 and name_theta + n * u["theta"] > name_theta_cap:
            return "concentration"
        return None

    n = 0
    while n < max_units:
        reason = breaks(n + 1)
        if reason:
            return n, reason
        n += 1
    return n, None


def _describe(
    s: dict[str, Any],
    u: dict[str, Any],
    entry: dict[str, Any],
    underlying: str,
    units: int,
    binding: str | None,
) -> dict[str, Any]:
    return {
        "underlying": underlying,
        "kind": s["kind"],
        "alternative_to": s.get("alternative_to"),
        "expiry": entry.get("expiry"),
        "dte": entry.get("dte"),
        "spot": entry.get("spot"),
        "legs": [
            {
                "option": o.get("option"),
                "strike": o["strike"],
                "right": o["right"],
                "quantity": q,
                "bid": o["bid"],
                "ask": o["ask"],
                "mid": round(o["mid"], 4),
                "delta": round(o["delta"], 3),
                "iv": round(o["iv"], 4),
            }
            for o, q in s["legs"]
        ],
        "units": units,
        "binding": binding,
        "per_unit": {
            "credit": round(u["credit"], 2),
            "natural": round(u["natural"], 2),
            "cash": round(u["credit"] * MULTIPLIER, 2),
            "max_loss": None if u["max_loss"] is None else round(u["max_loss"], 2),
            "buying_power": None if u["bp"] is None else round(u["bp"], 2),
            "stress_loss": None if u["stress_loss"] is None else round(u["stress_loss"], 2),
            "theta": round(u["theta"], 2),
            "bwd": None if u["bwd"] is None else round(u["bwd"], 2),
        },
        "theta_per_bp": round(u["theta"] / u["bp"], 5) if u["bp"] else None,
        "earnings_date": entry.get("earnings_date"),
        "earnings_unknown": entry.get("earnings_date") is None,
        "beta": entry.get("beta"),
    }


# --------------------------------------------------------------------------- the whole list
def suggest(
    income: dict[str, Any] | None,
    book: dict[str, Any],
    trades: list[dict[str, Any]],
    session: date,
    rules: dict[str, Any],
    account_type: dict[str, Any] | None,
    benchmark_spot: float | None,
) -> dict[str, Any] | None:
    cfg = rules["portfolio"]["candidates"]
    if cfg.get("enabled") is False:
        return None
    if not income or not income.get("symbols"):
        return {
            "error": (income or {}).get("error") or "the snapshot carries no income reading",
            "fits": [],
            "too_big": [],
        }
    rate = float(rules["pricing"]["rate"])
    basis = account_basis(account_type)
    shares: dict[str, float] = {}
    for t in trades:
        for leg in t.get("legs") or []:
            if leg.get("right") not in ("C", "P"):
                shares[t["underlying"]] = shares.get(t["underlying"], 0.0) + float(leg.get("quantity") or 0.0)
    excluded: dict[str, int] = {}

    def drop(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    fits, too_big = [], []
    for sym, entry in sorted(income["symbols"].items()):
        if entry.get("error"):
            drop("no chain")
            continue
        exp = entry.get("expiry")
        er = entry.get("earnings_date")
        if er and exp and session.isoformat() <= er <= exp:
            drop("earnings inside the trade")
            continue
        options = priced_options(entry, session, rate)
        spot, beta = _f(entry.get("spot")), _f(entry.get("beta"))
        scale = beta * spot / benchmark_spot if beta is not None and spot and benchmark_spot else None
        for s in structures(entry, options, cfg, shares.get(sym, 0.0)):
            if any(not o.get("passes") for o, _ in s["legs"]):
                drop("a leg is illiquid or unquoted")
                continue
            s["min_credit_share"] = float(cfg["spread_min_credit"])
            u = unit(s, entry, basis, scale)
            if u is None:
                drop("credit under a third of the width")
                continue
            units, binding = size(u, book, sym, cfg, basis, int(s.get("max_units", cfg["max_units"])))
            row = _describe(s, u, entry, sym, units, binding)
            if units >= 1:
                fits.append(row)
                continue
            if s["kind"] in ("short_put", "put_spread", "call_spread"):
                # The nearest version that fits: the same short strike, the widest long under the
                # per-trade cap. Offered in the fits list itself when it sizes to a unit or more.
                cap = float(cfg["per_trade_risk_pct"]) * (book.get("net_liq") or 0.0)
                min_share = float(cfg["spread_min_credit"])
                alt = fitted_spread(options, s["legs"][0][0], cap, min_share, s["kind"])
                same = alt and s["kind"] != "short_put" and alt["legs"][1][0] is s["legs"][1][0]
                if alt and not same and all(o.get("passes") for o, _ in alt["legs"]):
                    alt["min_credit_share"] = min_share
                    au = unit(alt, entry, basis, scale)
                    if au is not None:
                        a_units, a_binding = size(au, book, sym, cfg, basis, int(cfg["max_units"]))
                        alt_row = _describe(alt, au, entry, sym, a_units, a_binding)
                        row["alternative"] = alt_row
                        if a_units >= 1:
                            fits.append(alt_row)
            too_big.append(row)

    def rank(r: dict) -> float:
        return r["theta_per_bp"] if r["theta_per_bp"] is not None else -math.inf

    fits.sort(key=rank, reverse=True)
    too_big.sort(key=rank, reverse=True)
    return {
        "source_session": income.get("session"),
        "taken_at": income.get("taken_at"),
        "complete": income.get("complete"),
        "account_basis": basis,
        "names": len(income["symbols"]),
        "excluded": excluded,
        "fits": fits[: int(cfg["show"])],
        "fits_total": len(fits),
        "too_big": too_big[: int(cfg["show_too_big"])],
        "too_big_total": len(too_big),
        "error": None,
    }
