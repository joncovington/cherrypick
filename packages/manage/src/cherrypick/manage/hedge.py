"""Size an XSP vertical that brings the book's beta-weighted delta back toward its band.

Piece 3 of docs/portfolio-guidance-plan.md. Offered only when `portfolio.delta` fired, from the one
quoted XSP expiry the snapshot carries (`hedge_chain`), so a past session re-evaluates to the same
suggestion. Two kinds, each shown with what it does to the whole book:

- a CREDIT spread on the side the book leans away from (too long: sell a call spread). It cuts delta
  and adds theta, and adds the short gamma that comes with theta;
- a DEBIT spread on the side the book leans toward (too long: buy a put spread). It cuts delta and
  usually costs a little theta; with skew it can cost none (the 760/755 XSP put spread on 2026-10-18
  was +$0.12 a day), so the figure is stated, never assumed.

A hedge ties up its max loss like any trade, so its size is also held under the total buying-power
cap, and one that cannot fit a single spread says so.

Each leg's IV is solved from its own mid and priced by the same model as the portfolio reading, so the
before and after are one model's numbers. The price is the MID, which is not a fill: each suggestion
also states the natural price (sell at the bid, buy at the ask). A vertical is defined-risk, so it ties
up its max loss in a cash account, an IRA and a margin account alike.

It sizes and describes. Nothing here places an order.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from cherrypick.manage import portfolio as _portfolio
from cherrypick.manage import pricing

MULTIPLIER = pricing.MULTIPLIER


def _f(value: Any) -> float | None:
    return _portfolio._f(value)


def _priced(chain: dict[str, Any], rate: float) -> list[dict[str, Any]]:
    """The chain's options with model greeks from each one's own mid; unpriceable ones dropped."""
    spot, dte = _f(chain.get("spot")), chain.get("dte")
    if not spot or not dte or dte <= 0:
        return []
    years = dte / 365.0
    out = []
    for o in chain.get("options") or []:
        mid, strike = _f(o.get("mid")), _f(o.get("strike"))
        if mid is None or mid <= 0 or strike is None or o.get("right") not in ("C", "P"):
            continue
        iv = pricing.implied_vol(spot, strike, years, mid, o["right"], rate)
        g = pricing.greeks(spot, strike, years, iv, o["right"], rate) if iv else None
        if g is None:
            continue
        out.append(
            {
                **o,
                "strike": strike,
                "mid": mid,
                "bid": _f(o.get("bid")),
                "ask": _f(o.get("ask")),
                "iv": iv,
                **g,
            }
        )
    return out


def _nearest_delta(options: list[dict[str, Any]], right: str, target: float) -> dict[str, Any] | None:
    pool = [o for o in options if o["right"] == right]
    return min(pool, key=lambda o: abs(abs(o["delta"]) - target)) if pool else None


def _wing(options: list[dict[str, Any]], anchor: dict[str, Any], width: float, outward: float) -> dict | None:
    """The strike `width` further out from `anchor` (outward +1 = higher strikes), else the nearest
    listed strike beyond it in that direction."""
    want = anchor["strike"] + outward * width
    pool = [o for o in options if o["right"] == anchor["right"] and (o["strike"] - want) * outward >= -1e-9]
    return min(pool, key=lambda o: abs(o["strike"] - want)) if pool else None


def _structure(kind: str, need: float, options: list[dict[str, Any]], cfg: dict[str, Any]) -> tuple | None:
    """(name, [(option, signed quantity)]) for one unit, or None when the chain cannot build it."""
    width = float(cfg["width"])
    reduce = need < 0  # the book is too long: the hedge must be short delta
    if kind == "credit":
        right = "C" if reduce else "P"
        short = _nearest_delta(options, right, float(cfg["credit_short_delta"]))
        long_ = _wing(options, short, width, 1.0 if right == "C" else -1.0) if short else None
        name = "call credit spread" if right == "C" else "put credit spread"
        return (name, [(short, -1), (long_, 1)]) if short and long_ else None
    right = "P" if reduce else "C"
    long_ = _nearest_delta(options, right, float(cfg["debit_long_delta"]))
    short = _wing(options, long_, width, 1.0 if right == "C" else -1.0) if long_ else None
    name = "put debit spread" if right == "P" else "call debit spread"
    return (name, [(long_, 1), (short, -1)]) if short and long_ else None


def suggest(
    reading: dict[str, Any],
    chain: dict[str, Any] | None,
    metrics: dict[str, Any],
    session: date,
    rules: dict[str, Any],
) -> dict[str, Any] | None:
    """Hedge suggestions when `portfolio.delta` fired, a record of why none could be sized, or None
    when the book is inside its band."""
    cfg = rules["portfolio"]["hedge"]
    if cfg.get("enabled") is False or not any(
        g["rule"] == "portfolio.delta" for g in reading.get("guidance") or []
    ):
        return None
    band = _portfolio.delta_band(reading, rules["portfolio"]["delta"])
    bwd, bench_spot = reading.get("bwd"), reading.get("benchmark_spot")
    if band is None or bwd is None or not bench_spot:
        return {"error": "no beta-weighted delta or band to size against", "suggestions": []}
    lo, hi = band
    inset = float(cfg["inset"]) * (hi - lo)
    target = hi - inset if bwd > hi else lo + inset
    need = target - bwd
    out: dict[str, Any] = {
        "target": round(target, 2),
        "need": round(need, 2),
        "root": (chain or {}).get("root"),
        "expiration": (chain or {}).get("expiration"),
        "dte": (chain or {}).get("dte"),
        "suggestions": [],
        "error": None,
    }
    if not chain or not chain.get("options"):
        out["error"] = (chain or {}).get("error") or "the snapshot carries no hedge chain"
        return out
    root_beta = _f(((metrics or {}).get(chain["root"]) or {}).get("beta"))
    spot = _f(chain.get("spot"))
    if root_beta is None or not spot:
        out["error"] = f"no beta or price for {chain['root']}"
        return out
    scale = root_beta * spot / bench_spot
    rate = float(rules["pricing"]["rate"])
    options = _priced(chain, rate)
    for kind in ("credit", "debit"):
        built = _structure(kind, need, options, cfg)
        if built is None:
            out["suggestions"].append({"kind": kind, "error": "the chain has no strikes to build it"})
            continue
        out["suggestions"].append(_size(kind, *built, need, scale, reading, rules, chain))
    return out


def _size(
    kind: str,
    name: str,
    legs: list[tuple[dict[str, Any], int]],
    need: float,
    scale: float,
    reading: dict[str, Any],
    rules: dict[str, Any],
    chain: dict[str, Any],
) -> dict[str, Any]:
    cfg = rules["portfolio"]["hedge"]
    unit_delta = sum(q * MULTIPLIER * o["delta"] for o, q in legs)
    unit_bwd = unit_delta * scale
    unit_theta = sum(q * MULTIPLIER * o["theta"] for o, q in legs)
    unit_gamma = sum(q * MULTIPLIER * o["gamma"] for o, q in legs) * scale * scale
    # Signed price per share: + is a credit received.
    mid = -sum(q * o["mid"] for o, q in legs)
    natural = None
    if all(o["bid"] is not None and o["ask"] is not None for o, _ in legs):
        natural = -sum(q * (o["ask"] if q > 0 else o["bid"]) for o, q in legs)
    width = abs(legs[0][0]["strike"] - legs[1][0]["strike"])
    unit_max_loss = (width - mid) * MULTIPLIER if mid > 0 else -mid * MULTIPLIER

    if unit_bwd == 0 or unit_bwd * need < 0:
        return {"kind": kind, "structure": name, "error": "the spread does not move delta the way needed"}
    units = max(1, min(int(cfg["max_units"]), round(abs(need) / abs(unit_bwd))))
    # A hedge ties up its max loss like any trade, so it never takes the account past the total
    # buying-power cap; one that cannot fit a single unit says so rather than suggesting it anyway.
    net_liq, used = reading.get("net_liq"), reading.get("buying_power_used")
    if net_liq and used is not None and unit_max_loss > 0:
        room = float(cfg["max_buying_power_pct"]) * net_liq - used
        fits = int(room // unit_max_loss)
        if fits < 1:
            return {
                "kind": kind,
                "structure": name,
                "error": f"one spread ties up ${unit_max_loss:,.0f}, past the "
                f"{float(cfg['max_buying_power_pct']):.0%} buying-power cap (${max(room, 0):,.0f} left)",
            }
        units = min(units, fits)

    bwd = reading["bwd"]
    after_bwd = bwd + units * unit_bwd
    theta = reading.get("theta") or 0.0
    after_theta = theta + units * unit_theta
    # The band scales with theta, and the hedge changes theta, so "back in band" is judged on the band
    # the book would have after it.
    band = _portfolio.delta_band({**reading, "theta": after_theta}, rules["portfolio"]["delta"])
    gamma = reading.get("gamma_bench")
    after_gamma = gamma + units * unit_gamma if gamma is not None else None
    max_loss = units * unit_max_loss
    return {
        "kind": kind,
        "structure": name,
        "units": units,
        "legs": [
            {
                "symbol": o.get("symbol"),
                "label": f"{chain['root']} {chain['expiration']} {o['strike']:g}{o['right']}",
                "quantity": q * units,
                "strike": o["strike"],
                "right": o["right"],
                "bid": o["bid"],
                "ask": o["ask"],
                "mid": o["mid"],
                "delta": round(o["delta"], 3),
                "iv": round(o["iv"], 4),
            }
            for o, q in legs
        ],
        "price": round(mid, 2),
        "price_label": f"{abs(mid):.2f} {'cr' if mid > 0 else 'db'}",
        "natural": round(natural, 2) if natural is not None else None,
        "cash": round(units * mid * MULTIPLIER, 2),
        "max_loss": round(max_loss, 2),
        "bwd_change": round(units * unit_bwd, 2),
        "theta_change": round(units * unit_theta, 2),
        "after": {
            "bwd": round(after_bwd, 2),
            "in_band": band is not None and band[0] <= after_bwd <= band[1],
            "theta": round(after_theta, 2),
            "theta_pct_net_liq": round(after_theta / net_liq, 5) if net_liq else None,
            "breakeven": _portfolio._breakeven(after_theta, after_gamma, reading.get("benchmark_spot")),
            "buying_power_used": round(used + max_loss, 2) if used is not None else None,
            "buying_power_used_pct": round((used + max_loss) / net_liq, 4)
            if used is not None and net_liq
            else None,
        },
    }
