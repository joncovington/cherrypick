"""Whether an option, or a spread, is liquid enough to trade, and the price a working order gets.

One home for the screener's liquidity bars, so the readings that judge the income lists
(`scripts/fetch_screener_greeks.py`) and the candidates manage sizes for the account
(`cherrypick.manage.candidates`) cannot disagree about what "liquid" means.

**The bars** (chosen 2026-10-16 from six sessions of regular-hours readings): a leg's spread at most
10% of its mid or a nickel, and at least 100 open. **Volume is a flag, not a bar** (2026-10-19): the
readings are taken at 11:30 and 14:30 ET, and a part-day count fails names that are tight and open --
at 11:30, 15% of the cycle-monthly short puts failed on volume alone, with a median open interest of
466, and 9.6% at 14:30. A leg under `min_volume` still passes and is marked `quiet`.

**A spread is judged as one order** (2026-10-19). What a vertical costs to trade is its own spread:
the mid credit less the natural one (sell at the bid, pay the ask), half the legs' widths added. Over
seven sessions of cycle-monthly credit spreads that cost was a median 7.5% of the credit, and a third
of the spreads costing 10% or less failed a bar on one leg -- usually the cheap long, whose spread is
wide in percent and small in dollars. So a spread passes when that cost is within `max_spread_pct` of
its mid credit (or `max_spread_abs`) and each leg has its open interest; its legs' own spreads are not
judged one by one.

**The working price** is halfway from the mid to the natural fill: a limit at the mid stepped down a
quarter of the spread, the price a patient order commonly gets. For one short option it is the mid
less a quarter of the width; for a spread, the mid credit less a quarter of the legs' widths added.
It is a model of a fill, not a fill -- it is the figure the one-third credit floor is tested at, so a
floor that holds only at the mid is not offered as one.

Pure: quotes in, verdicts out.
"""

from __future__ import annotations

LEG_BARS: dict = {
    "max_spread_pct": 0.10,
    "max_spread_abs": 0.05,
    "min_open_interest": 100,
    "min_volume": 10,
}
# A cent-priced width compared in floats: 0.23 - 0.18 is 0.0500000000000000017, one nickel.
EPS = 1e-9


def two_sided(q: dict | None) -> bool:
    """A quote with a bid above zero and an ask at or above it."""
    q = q or {}
    bid, ask = q.get("bid"), q.get("ask")
    return bid is not None and ask is not None and bid > 0 and ask >= bid


def _priced(q: dict | None, n: int) -> bool:
    if n < 0:
        return two_sided(q)
    q = q or {}
    bid, ask = q.get("bid"), q.get("ask")
    return bid is not None and ask is not None and ask > 0 and ask >= bid >= 0


def _tight(width: float, mid: float, bars: dict) -> bool:
    return width <= bars["max_spread_abs"] + EPS or (mid > 0 and width / mid <= bars["max_spread_pct"] + EPS)


def _open(q: dict, bars: dict) -> bool:
    return (q.get("open_interest") or 0) >= bars["min_open_interest"]


def leg_passes(q: dict | None, bars: dict) -> bool:
    """One option against the bars: two-sided, tight enough on either rule (percent of mid OR
    absolute), with enough open interest. Volume is not a bar (`quiet`)."""
    if not two_sided(q):
        return False
    bid, ask = q["bid"], q["ask"]
    return _tight(ask - bid, (ask + bid) / 2, bars) and _open(q, bars)


def quiet(q: dict | None, bars: dict) -> bool:
    """Traded fewer than `min_volume` contracts so far in the session: a flag beside the verdict."""
    return ((q or {}).get("volume") or 0) < bars["min_volume"]


def structure_prices(legs: list[tuple[dict, int]]) -> dict | None:
    """Per share, signed so a credit is +: the mid, the natural fill (sell at the bid, pay the ask),
    the working price halfway between, and the cost of trading it (mid less natural). `legs` is
    (quote, signed quantity per unit), -1 for a sold option. None unless every sold leg is two-sided
    and every bought one has an ask (a cheap wing bid at zero can still be bought)."""
    if not legs or not all(_priced(q, n) for q, n in legs):
        return None
    mid = -sum(n * (q["bid"] + q["ask"]) / 2 for q, n in legs)
    natural = -sum(n * (q["ask"] if n > 0 else q["bid"]) for q, n in legs)
    return {
        "mid": round(mid, 4),
        "natural": round(natural, 4),
        "working": round((mid + natural) / 2, 4),
        "cost": round(mid - natural, 4),
    }


def structure_passes(legs: list[tuple[dict, int]], bars: dict) -> bool:
    """A structure against the bars as one order. One leg is judged as a leg. More than one: every
    leg two-sided with its open interest, and the structure's cost (mid less natural) within
    `max_spread_pct` of its mid's size or `max_spread_abs`."""
    if len(legs) == 1:
        return leg_passes(legs[0][0], bars)
    p = structure_prices(legs)
    if p is None or not all(_open(q, bars) for q, _ in legs):
        return False
    return _tight(p["cost"], abs(p["mid"]), bars)


def working_premium(q: dict | None) -> float | None:
    """What one sold option is modelled to fill at: the mid less a quarter of the width."""
    if not two_sided(q):
        return None
    return round((q["bid"] + q["ask"]) / 2 - (q["ask"] - q["bid"]) / 4, 4)
