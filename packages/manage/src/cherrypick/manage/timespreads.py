"""The time-spread scanner's pure parts (docs/time-spread-scanner-plan.md).

A calendar or diagonal sells a front expiry against a later one; the scanner looks for names whose
front week is priced rich to the back month. The hypothesis behind it -- a front-week premium the
stock does not then earn -- is UNTESTED (item 54), so rows are ordered by the front/back ratio and say
so, never ranked as if it were an edge.

This module reads nothing and writes nothing: `scripts/fetch_time_spreads.py` makes the broker reads
and hands the plain rows here.

Step 1, the term-structure read: from each name's market metrics, the front IV (the expiry
`front_days` out nearest the middle of that range), the back IV (a standard monthly `back_days` out
nearest the middle, else any expiry there), their ratio, and whether the name passes the prefilter.

Step 2, the structures (`structures`, `build_all`): from the shortlist's quoted chains, same-strike
put and call calendars at the money and one listed strike either side, and put and call diagonals
(the long `long_delta`, the short `short_delta` beyond both the long's strike and the stock, the debit
at most `max_debit_share` of the gap). Each is judged as one order against the leg bars and the
`max_cost_share` gate, and carries its MODELLED columns: Black-Scholes at each leg's own IV solved
from its mid, the long valued at the short's expiry at its current IV.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from cherrypick.core import calendar as _calendar
from cherrypick.core import legbars

from cherrypick.manage import candidates as _candidates
from cherrypick.manage import pricing

RULE: dict[str, Any] = {
    "front_days": [5, 16],
    "back_days": [30, 75],
    "min_ratio": 1.15,
    "min_iv_rank": 0.30,
    # A stock's market cap floor; a fund has none and is kept.
    "min_market_cap": 20e9,
    "shortlist": 20,
    # Diagonals: the long's and the short's delta (absolute), and the debit's share of the strike gap
    # (manage's diagonal-width rule).
    "long_delta": [0.55, 0.75],
    "short_delta": [0.15, 0.35],
    "short_delta_target": 0.25,
    "max_debit_share": 0.75,
    # The natural fill's distance from the mid, as a share of the debit.
    "max_cost_share": 0.15,
    # Net delta per unit (shares) within which a structure is neutral.
    "neutral_delta": 10.0,
}

HYPOTHESIS = "ordered by the front/back IV ratio: a hypothesis, untested (item 54)"


def _f(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def is_monthly(day: date) -> bool:
    """A standard monthly expiry: the third Friday, or the trading day before it when that is a holiday."""
    friday = date(day.year, day.month, 15)
    while friday.weekday() != 4:
        friday += timedelta(days=1)
    if not _calendar.is_trading_day(friday):
        friday = _calendar.previous_trading_day(friday)
    return day == friday


def pick(expiries: list[tuple[date, float]], session: date, days: list[int], monthly: bool) -> dict | None:
    """The expiry inside `days` nearest the middle of the range, a standard monthly first when
    `monthly`: {expiration, dte, iv}; None when none lists an IV there."""
    lo, hi = int(days[0]), int(days[1])
    inside = [(d, iv) for d, iv in expiries if iv and lo <= (d - session).days <= hi]
    if monthly and any(is_monthly(d) for d, _ in inside):
        inside = [(d, iv) for d, iv in inside if is_monthly(d)]
    if not inside:
        return None
    mid = (lo + hi) / 2
    d, iv = min(inside, key=lambda e: (abs((e[0] - session).days - mid), e[0]))
    return {"expiration": d.isoformat(), "dte": (d - session).days, "iv": iv}


def term_row(m: dict[str, Any], session: date, rule: dict[str, Any] = RULE) -> dict[str, Any]:
    """One name's term-structure row from its metrics (plain values: `symbol`, `iv_rank` a decimal,
    `iv_30d` and `hv_30d` in percent as the broker states them, `market_cap` dollars, `earnings_date`,
    `expiries` [(date or ISO string, decimal IV)]), judged against the prefilter: `passes`, and `why`
    it does not."""
    expiries = []
    for d, iv in m.get("expiries") or []:
        day, vol = _date(d), _f(iv)
        if day and vol and vol > 0:
            expiries.append((day, vol))
    front = pick(expiries, session, rule["front_days"], monthly=False)
    back = pick(expiries, session, rule["back_days"], monthly=True)
    ratio = round(front["iv"] / back["iv"], 4) if front and back else None
    cap, ivr = _f(m.get("market_cap")), _f(m.get("iv_rank"))
    er = _date(m.get("earnings_date"))
    why = []
    if not front or not back:
        why.append("no front or back expiry with an IV")
    elif ratio < float(rule["min_ratio"]):
        why.append(f"front/back ratio under {float(rule['min_ratio']):g}")
    if ivr is None or ivr < float(rule["min_iv_rank"]):
        why.append(f"IV rank under {float(rule['min_iv_rank']):.0%}")
    if cap and cap < float(rule["min_market_cap"]):
        why.append(f"market cap under ${float(rule['min_market_cap']) / 1e9:g}B")
    if er and back and session <= er <= _date(back["expiration"]):
        why.append("earnings before the back expiry")
    return {
        "symbol": str(m.get("symbol") or "").upper(),
        "front": front,
        "back": back,
        "ratio": ratio,
        "iv_rank": ivr,
        "iv_30d": _f(m.get("iv_30d")),
        "hv_30d": _f(m.get("hv_30d")),
        "market_cap": cap,
        "fund": not cap,
        "earnings_date": er.isoformat() if er else None,
        "sector": m.get("sector"),
        "passes": not why,
        "why": why,
    }


def shortlist(rows: list[dict[str, Any]], rule: dict[str, Any] = RULE) -> list[str]:
    """The passing names with the highest ratios, at most `shortlist` of them, best first (by name on
    a tie). The order is the untested hypothesis (`HYPOTHESIS`), not an edge."""
    passing = [r for r in rows if r["passes"]]
    passing.sort(key=lambda r: (-r["ratio"], r["symbol"]))
    return [r["symbol"] for r in passing[: int(rule["shortlist"])]]


# --------------------------------------------------------------------------- step 2: the structures
KINDS = ("put calendar", "call calendar", "put diagonal", "call diagonal")


def _priced(view: dict[str, Any] | None, spot: float, session: date, rate: float) -> dict:
    """{(right, strike): option with mid, IV and model greeks} for one expiry's quoted options."""
    if not view:
        return {}
    entry = {"spot": spot, "dte": view.get("dte"), "options": view.get("options") or []}
    return {(o["right"], o["strike"]): o for o in _candidates.priced_options(entry, session, rate)}


def _roll_day(front: date, days: int) -> str:
    """The trading day `days` before the front expiry: where the expiry close makes the short due."""
    day = front - timedelta(days=days)
    return (day if _calendar.is_trading_day(day) else _calendar.previous_trading_day(day)).isoformat()


def _model(legs, spot, front_dte, rate, debit_per_share):
    """(P&L at the short's expiry at a price, the leg dicts the band search reads). Modelled."""
    horizon = front_dte / 365.0
    model = [
        {"quantity": n, "strike": o["strike"], "right": o["right"], "iv": o["iv"], "years": dte / 365.0}
        for o, n, dte in legs
    ]

    def pnl(s: float) -> float:
        value = sum(pricing.leg_value_at(m, s, m["years"] - horizon, rate) for m in model)
        return value - debit_per_share * pricing.MULTIPLIER

    return pnl, model, horizon


def describe(
    kind: str,
    legs: list[tuple[dict, int, int]],
    spot: float,
    front: dict,
    back: dict,
    rules: dict[str, Any],
    rule: dict[str, Any] = RULE,
) -> tuple[dict | None, str | None]:
    """(row, None) for one structure that clears its gates, or (None, the reason it does not). `legs`
    is (option, signed quantity per unit, its days to expiry); one unit is one contract a leg."""
    rate = float(rules["pricing"]["rate"])
    quotes = [(o, n) for o, n, _ in legs]
    p = legbars.structure_prices(quotes)
    if p is None:
        return None, "a leg is unquoted"
    debit = -p["mid"]
    if debit <= 0:
        return None, "no debit to pay"
    strikes = sorted({o["strike"] for o, _, _ in legs})
    gap = strikes[-1] - strikes[0]
    if "diagonal" in kind and (gap <= 0 or debit > float(rule["max_debit_share"]) * gap + 1e-9):
        return None, f"debit over {float(rule['max_debit_share']):.0%} of the strike gap"
    if p["cost"] > float(rule["max_cost_share"]) * debit + 1e-9:
        return None, f"natural fill over {float(rule['max_cost_share']):.0%} of the debit from the mid"
    if not legbars.structure_passes(quotes, {**legbars.LEG_BARS}):
        return None, "fails the leg bars as one order"
    pnl, model, horizon = _model(legs, spot, front["dte"], rate, debit)
    delta = sum(n * o["delta"] for o, n, _ in legs) * pricing.MULTIPLIER
    theta = sum(n * o["theta"] for o, n, _ in legs) * pricing.MULTIPLIER
    neutral = float(rule["neutral_delta"])
    db = rules["debit"]
    gain = float(((db["take_profit"].get("by_shape") or {}).get("calendar") or {}).get("gain_fraction", 0.25))
    if "calendar" not in kind:
        gain = float(db["take_profit"]["gain_fraction"])
    loss_share = float(db.get("price_stop", {}).get("loss_share", 0.25))
    be = pricing.price_band(model, spot, horizon, rate, debit * pricing.MULTIPLIER)
    stop = pricing.price_band(model, spot, horizon, rate, debit * (1 - loss_share) * pricing.MULTIPLIER)
    flat = pnl(spot)
    long_leg = next(o for o, n, _ in legs if n > 0)
    short_leg = next(o for o, n, _ in legs if n < 0)
    return {
        "kind": kind,
        "direction": "neutral" if abs(delta) <= neutral else ("bullish" if delta > 0 else "bearish"),
        "front": front["expiration"],
        "back": back["expiration"],
        "long": {"strike": long_leg["strike"], "right": long_leg["right"], "option": long_leg.get("option")},
        "short": {
            "strike": short_leg["strike"],
            "right": short_leg["right"],
            "option": short_leg.get("option"),
        },
        "label": f"{kind} {front['expiration']}/{back['expiration']} "
        f"{short_leg['strike']:g}/{long_leg['strike']:g}{long_leg['right']}",
        "debit_mid": round(debit, 2),
        "debit_natural": round(-p["natural"], 2),
        "gap": round(gap, 2) if gap else None,
        "debit_share_of_gap": round(debit / gap, 4) if gap else None,
        "front_iv": round(short_leg["iv"], 4),
        "back_iv": round(long_leg["iv"], 4),
        "net_delta": round(delta, 1),
        "theta_day": round(theta, 2),
        "pnl_flat": round(flat, 2),
        "pnl_up_5": round(pnl(spot * 1.05), 2),
        "pnl_down_10": round(pnl(spot * 0.90), 2),
        "loses_unchanged": flat < 0,
        "breakeven": list(be) if be else None,
        "price_stop": list(stop) if stop else None,
        "take_profit": round(debit * (1 + gain), 2),
        "value_stop": round(debit * (1 - float(db["stop"]["loss_fraction"])), 2),
        "roll_by": _roll_day(date.fromisoformat(front["expiration"]), int(rules["expiry"]["close_by_dte"])),
        "max_loss": round(debit * pricing.MULTIPLIER, 2),
        "modelled": True,
    }, None


def structures(
    entry: dict[str, Any], session: date, rules: dict[str, Any], rule: dict[str, Any] = RULE
) -> tuple[list[dict], dict[str, int]]:
    """(rows, {reason: count dropped}) for one name's quoted front and back chains."""
    spot = _f(entry.get("spot"))
    front, back = entry.get("front"), entry.get("back")
    dropped: dict[str, int] = {}
    if entry.get("error") or not spot or not front or not back:
        return [], {entry.get("error") or "no chain": 1}
    rate = float(rules["pricing"]["rate"])
    F, B = _priced(front, spot, session, rate), _priced(back, spot, session, rate)
    fd, bd = int(front["dte"]), int(back["dte"])
    rows: list[dict] = []

    def add(kind, legs):
        row, why = describe(kind, legs, spot, front, back, rules, rule)
        if row:
            rows.append(row)
        else:
            dropped[why] = dropped.get(why, 0) + 1

    # Calendars: the listed strike nearest the stock that both expiries quote, and one either side.
    for right, kind in (("P", "put calendar"), ("C", "call calendar")):
        common = sorted(k for (r, k) in F if r == right and (right, k) in B)
        if not common:
            continue
        i = min(range(len(common)), key=lambda j: (abs(common[j] - spot), common[j]))
        for k in common[max(i - 1, 0) : i + 2]:
            add(kind, [(B[(right, k)], 1, bd), (F[(right, k)], -1, fd)])
    # Diagonals: each long in its delta range, against the short nearest the target beyond it.
    lo, hi = (float(x) for x in rule["long_delta"])
    slo, shi = (float(x) for x in rule["short_delta"])
    target = float(rule["short_delta_target"])
    for right, kind in (("C", "call diagonal"), ("P", "put diagonal")):
        longs = [o for (r, _), o in B.items() if r == right and lo <= abs(o["delta"]) <= hi]
        for lg in sorted(longs, key=lambda o: o["strike"]):
            # The short sits beyond both the long's strike and the stock, on the trade's own side.
            edge = max(lg["strike"], spot) if right == "C" else min(lg["strike"], spot)
            shorts = [
                o
                for (r, _), o in F.items()
                if r == right
                and (o["strike"] > edge if right == "C" else o["strike"] < edge)
                and slo <= abs(o["delta"]) <= shi
            ]
            if not shorts:
                dropped["no short in its delta range beyond the long"] = (
                    dropped.get("no short in its delta range beyond the long", 0) + 1
                )
                continue
            sh = min(shorts, key=lambda o: (abs(abs(o["delta"]) - target), o["strike"]))
            add(kind, [(lg, 1, bd), (sh, -1, fd)])
    return rows, dropped


def build_all(
    doc: dict[str, Any], session: date, rules: dict[str, Any], rule: dict[str, Any] | None = None
) -> dict:
    """Every shortlisted name's structures from a day's reading, ordered by its ratio (the untested
    hypothesis), then by name and structure; with the reasons the rest were dropped."""
    rule = {**RULE, **(rule or doc.get("rule") or {})}
    by_name = {r["symbol"]: r for r in doc.get("rows") or []}
    rows: list[dict] = []
    dropped: dict[str, int] = {}
    for sym in doc.get("shortlist") or []:
        built, why = structures(
            (doc.get("chains") or {}).get(sym) or {"error": "no chain read"}, session, rules, rule
        )
        for k, v in why.items():
            dropped[k] = dropped.get(k, 0) + v
        term = by_name.get(sym) or {}
        for r in built:
            rows.append(
                {
                    "underlying": sym,
                    "ratio": term.get("ratio"),
                    "iv_rank": term.get("iv_rank"),
                    "sector": term.get("sector"),
                    "earnings_date": term.get("earnings_date"),
                    "spot": _f(((doc.get("chains") or {}).get(sym) or {}).get("spot")),
                    **r,
                }
            )
    rows.sort(key=lambda r: (-(r["ratio"] or 0), r["underlying"], KINDS.index(r["kind"]), r["label"]))
    return {"rows": rows, "dropped": dropped, "order": HYPOTHESIS}
