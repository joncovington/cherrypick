"""Ideas for reshaping the book, read off the risk matrix and the trades behind it.

Each idea is a header, a subheader with the figures, and a plain-English paragraph on how the change
would help the portfolio, plus, where the change is one the book can be repriced under (closing a
trade, resizing it, dropping a side), the matrix's row now before and after and the theta it moves.
The row is the realistic one (IV moved with the market as VIX has, `spotvol`) where there is one, IV
held otherwise, and says which. An effect can make a cell worse: dropping a side moves risk, it does
not remove it, and the figures say so rather than the prose.

Five kinds, each its own `portfolio.ideas` block, one card each:

- **index_bucket**: trades on broad indexes that move together (`names`) whose combined downside is
  over `max_pct_nlv` of net liq. Spread across tickers they are still one bet. Modelled: closing the
  biggest.
- **short_dated_theta**: one trade paying over `max_share` of the book's theta from a short leg
  `within_days` or less from expiry: income that ends with the leg, paid for by its gamma. Modelled:
  closing it.
- **trade_size**: trades whose downside is over the candidates' per-trade cap. Modelled: each resized
  to the cap, which is the change it asks for.
- **call_side**: credit trades selling both sides where the short calls' IV is at most
  `max_call_put_iv` of the short puts': the cheap side sold for a like-sized loss. The suite's own
  credit-spread studies (2026-10-22, items 14-16) found call spreads lost at every delta. Modelled:
  those trades without their calls.
- **expiry_cluster**: expiries whose trades can lose over `max_pct_nlv` of net liq together: a
  planning target for spreading entries, tighter than the `expiry_concentration` review. No model:
  the change is where the next trades go.

A trade one card already models closing is left off the later cards. Ideas inform; they size, place
and close nothing, and carry no confidence because none is a rule. The thresholds are ours, with no
published evidence behind them.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

ORDER = ("index_bucket", "short_dated_theta", "trade_size", "call_side", "expiry_cluster")
WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}

Price = Callable[[dict[int, float]], dict[str, Any]]


def _count(n: int, capital: bool = False) -> str:
    w = WORDS.get(n, str(n))
    return w[:1].upper() + w[1:] if capital else w


def _money(v: float) -> str:
    return f"${abs(v):,.0f}"


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%".replace(".0%", "%")


def _names(xs: list[str]) -> str:
    xs = list(xs)
    return xs[0] if len(xs) == 1 else f"{', '.join(xs[:-1])} and {xs[-1]}"


def _day(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d:%B}"


def _tid(t: dict[str, Any]) -> str:
    return str(t.get("trade_id") or t["underlying"])


def _down(t: dict[str, Any]) -> float | None:
    v = (t.get("profile") or {}).get("downside_from_now")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _what(t: dict[str, Any]) -> str:
    return f"{t['underlying']} {t.get('shape') or t.get('kind') or 'trade'}"


def _worst(row: list[float | None]) -> float | None:
    vals = [v for v in row if v is not None]
    return min(vals) if vals else None


def _close(*trades: dict[str, Any]) -> dict[int, float]:
    return {id(leg): 0.0 for t in trades for leg in t.get("legs") or []}


def _effect(
    base: dict[str, Any], price: Price, scale: dict[int, float], change: str, moves: list[float], basis: str
) -> dict[str, Any]:
    after = price(scale)
    return {
        "change": change,
        "basis": basis,
        "moves": moves,
        "before": base["pnl"],
        "after": after["pnl"],
        "worst_before": _worst(base["pnl"]),
        "worst_after": _worst(after["pnl"]),
        "theta_before": round(base["theta"], 2),
        "theta_after": round(after["theta"], 2),
    }


def suggest(
    reading: dict[str, Any],
    trades: list[dict[str, Any]],
    trade_theta: dict[int, float],
    price: Price,
    moves: list[float],
    basis: str,
    rules: dict[str, Any],
) -> list[dict[str, Any]]:
    """The ideas that apply to this book, in ORDER, at most `show`."""
    pcfg = rules["portfolio"]
    cfg = pcfg["ideas"]
    nlv = reading.get("net_liq")
    if not cfg["enabled"] or not nlv or nlv <= 0:
        return []
    base = price({})
    cap_pct = float(pcfg["candidates"]["per_trade_risk_pct"])
    cap = cap_pct * nlv
    found: dict[str, dict[str, Any]] = {}
    closed: set[str] = set()  # trades an earlier card already models closing

    # ---- broad indexes held more than once
    c = cfg["index_bucket"]
    if c["enabled"]:
        names = {str(n).upper() for n in c["names"]}
        held = sorted(
            (t for t in trades if str(t["underlying"]).upper() in names and _down(t) is not None),
            key=lambda t: _down(t),
        )
        unds = list(dict.fromkeys(t["underlying"] for t in held))
        total = -sum(_down(t) for t in held)
        if len(unds) >= 2 and total > float(c["max_pct_nlv"]) * nlv:
            big = held[0]
            also = (
                f" The {_what(big)} is over the per-trade cap on its own as well."
                if -_down(big) > cap
                else ""
            )
            n = _count(len(held))
            budget = _pct(float(c["max_pct_nlv"]))
            found["index_bucket"] = {
                "key": "index_bucket",
                "header": f"{_names(unds)} are one bet",
                "subheader": (
                    f"{n} trades can lose {_money(total)} together: {_pct(total / nlv)} of net liq, "
                    f"against a {budget} budget for broad-index risk"
                ),
                "explanation": (
                    "These indexes rise and fall together, so a sharp move reaches every one of "
                    f"these trades on the same day: they behave like one position {n} times the "
                    "size. Holding fewer of them, and putting the risk that frees up into names "
                    "that move differently (Treasuries, gold, energy, markets abroad), shrinks both "
                    f"edges of the matrix without giving up selling premium.{also}"
                ),
                "trades": [_tid(t) for t in held],
                "effect": _effect(base, price, _close(big), f"closing the {_what(big)}", moves, basis),
            }
            closed.add(_tid(big))

    # ---- one trade carrying the book's income from a leg about to expire
    c = cfg["short_dated_theta"]
    theta = float(reading.get("theta") or 0.0)
    if c["enabled"] and theta > 0:
        heavy = [
            t
            for t in trades
            if trade_theta.get(id(t), 0.0) > float(c["max_share"]) * theta
            and t.get("nearest_short_dte") is not None
            and t["nearest_short_dte"] <= int(c["within_days"])
        ]
        if heavy:
            t = max(heavy, key=lambda t: trade_theta.get(id(t), 0.0))
            th, dte, exp = trade_theta[id(t)], t["nearest_short_dte"], t.get("nearest_short_expiry")
            shorts = [
                leg
                for leg in t.get("legs") or []
                if (leg.get("quantity") or 0) < 0 and leg.get("expiration") == exp
            ]
            strike = (
                f"{shorts[0]['strike']:g}" if shorts and shorts[0].get("strike") is not None else "its strike"
            )
            days = "day" if dte == 1 else f"{_count(dte)} days" if dte <= 10 else f"{dte} days"
            found["short_dated_theta"] = {
                "key": "short_dated_theta",
                "header": f"Much of the income is {t['underlying']}'s last {days}",
                "subheader": f"the {_what(t)} pays {_money(th)} of the book's {_money(theta)} a day"
                + (f", from a short leg that expires {_day(exp)}" if exp else ""),
                "explanation": (
                    "An option loses value fastest in its final days, so this one short leg makes "
                    "the whole book's income look bigger than it is: once it expires the book earns "
                    f"about {_money(theta - th)} a day. That fast decay is paid for in risk, since "
                    f"the trade loses if {t['underlying']} moves well away from {strike} before "
                    "then. Judge the book's income without it, and give this trade its own exit "
                    "plan for its last days."
                ),
                "trades": [_tid(t)],
                "effect": _effect(base, price, _close(t), f"closing the {_what(t)}", moves, basis),
            }
            closed.add(_tid(t))

    # ---- trades over the per-trade cap, modelled at the cap
    c = cfg["trade_size"]
    if c["enabled"]:
        over = sorted(
            (t for t in trades if _down(t) is not None and -_down(t) > cap and _tid(t) not in closed),
            key=lambda t: _down(t),
        )
        if over:
            scale = {id(leg): cap / -_down(t) for t in over for leg in t.get("legs") or []}
            sizes = [f"{_money(-_down(t))} ({_pct(-_down(t) / nlv)})" for t in over]
            each = f"the {_what(over[0])} can lose {sizes[0]}" + "".join(
                f"{' and' if i == len(over) - 1 else ','} the {_what(t)} {s}"
                for i, (t, s) in enumerate(zip(over[1:], sizes[1:], strict=True), start=1)
            )
            one = len(over) == 1
            found["trade_size"] = {
                "key": "trade_size",
                "header": f"The {_what(over[0])} is bigger than one trade should be"
                if one
                else f"{_count(len(over), True)} trades are bigger than one trade should be",
                "subheader": f"{each}: over the cap of {_pct(cap_pct)} of net liq a trade ({_money(cap)})",
                "explanation": (
                    f"If {'this trade goes' if one else 'one of these goes'} fully wrong it costs "
                    "more than the plan allows any single idea to cost. Trading smaller (fewer "
                    "contracts, or narrower wings) keeps one bad call from setting the account back "
                    "weeks, and leaves room to act on the next one. The figures below show the book "
                    f"with {'it' if one else 'each'} sized down to the cap."
                ),
                "trades": [_tid(t) for t in over],
                "effect": _effect(
                    base, price, scale, f"{'it' if one else 'each'} sized down to {_money(cap)}", moves, basis
                ),
            }

    # ---- the cheap call side sold beside the rich put side
    c = cfg["call_side"]
    if c["enabled"]:
        picked, call_ivs, put_ivs, scale = [], [], [], {}
        for t in trades:
            if t.get("kind") != "credit":
                continue
            legs = t.get("legs") or []
            sc = [
                leg["iv"]
                for leg in legs
                if leg.get("right") == "C" and (leg.get("quantity") or 0) < 0 and leg.get("iv")
            ]
            sp = [
                leg["iv"]
                for leg in legs
                if leg.get("right") == "P" and (leg.get("quantity") or 0) < 0 and leg.get("iv")
            ]
            if not sc or not sp:
                continue
            ci, pi = sum(sc) / len(sc), sum(sp) / len(sp)
            if pi > 0 and ci / pi <= float(c["max_call_put_iv"]):
                picked.append(t)
                call_ivs.append(ci)
                put_ivs.append(pi)
                scale.update({id(leg): 0.0 for leg in legs if leg.get("right") == "C"})
        if picked:

            def span(xs: list[float]) -> str:
                lo, hi = round(min(xs) * 100), round(max(xs) * 100)
                return f"{lo}%" if lo == hi else f"{lo}-{hi}%"

            found["call_side"] = {
                "key": "call_side",
                "header": "The call side is the cheap side",
                "subheader": (
                    f"{_names([t['underlying'] for t in picked])}: calls sold at {span(call_ivs)} "
                    f"implied vol, puts at {span(put_ivs)}"
                ),
                "explanation": (
                    "Puts cost more than calls because investors pay up for crash protection, and "
                    "that extra is the premium worth selling. The call spreads here collect far less "
                    "for a loss of a similar size, and in the suite's own credit-spread studies call "
                    "spreads lost at every delta tested, since markets tend to drift up. Selling the "
                    "put side alone (or a smaller call side) on new trades removes most of the risk "
                    "to a rally, but moves some of it to a fall: pair it with less broad-index risk "
                    "in total, and accept a book that leans slightly bullish."
                ),
                "trades": [_tid(t) for t in picked],
                "effect": _effect(base, price, scale, "the same trades without their calls", moves, basis),
            }

    # ---- expiries carrying too much of the book
    c = cfg["expiry_cluster"]
    if c["enabled"]:
        heavy = sorted(
            (
                x
                for x in (reading.get("ladder") or {}).get("expiries") or []
                if len(x.get("trades") or []) >= 2
                and -float(x.get("downside") or 0.0) > float(c["max_pct_nlv"]) * nlv
            ),
            key=lambda x: x["expiration"],
        )
        if heavy:
            parts = "; ".join(
                f"{_day(x['expiration'])}: {_count(len(x['trades']))} trades, "
                f"{_money(-float(x['downside']))} ({_pct(-float(x['downside']) / nlv)})"
                for x in heavy
            )
            first = heavy[0]
            target = _pct(float(c["max_pct_nlv"]))
            found["expiry_cluster"] = {
                "key": "expiry_cluster",
                "header": f"{_count(len(first['trades']), True)} trades expire on {_day(first['expiration'])}"
                if len(heavy) == 1
                else f"{_count(len(heavy), True)} expiry dates each carry too much of the book",
                "subheader": f"{parts}: over the {target} planning target for one expiry",
                "explanation": (
                    "Everything that expires on the same day meets the same gap and the same closing "
                    "price. Placing new trades in the next monthly cycles instead (one batch a month, "
                    "still 45 to 60 days out) means one bad day reaches one batch rather than the "
                    "whole book, and spreads the work of managing them across the month."
                ),
                "trades": [tid for x in heavy for tid in x["trades"]],
                "effect": None,
            }

    out = [found[k] for k in ORDER if k in found]
    for idea in out:
        idea["subheader"] = idea["subheader"][:1].upper() + idea["subheader"][1:]
    return out[: int(cfg["show"])]
