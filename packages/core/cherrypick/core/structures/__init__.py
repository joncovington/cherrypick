"""cherrypick.core.structures — shared option-structure arithmetic.

One home for the small pure formulas two trading modules would otherwise each carry a copy of (the
core bar: two packages must never be able to disagree about what a number means). First resident is
the straddle-based expected move, previously computed independently by the earnings scanner and its
double-calendar order builder, and now also the strike-targeting input for the weekly calendars
module — three call sites, one 0.85, no drift. Second (2026-09-18): the limit-price tick rounding
every live order builder needs, which meic and flies each carried. Third (2026-11-20): whether a
structure's rule enters for a credit or a debit (`entry_kind`), which the backtest's settings grid
reads to offer only the stop a structure can fire and the Builder reads to word its exits.

Everything here is a pure function over floats the caller already fetched: no I/O, no broker, no
cache reads, so it is safe on any loop-decision path.
"""

from __future__ import annotations

from collections.abc import Mapping

# The standard straddle-to-expected-move correction. An ATM straddle's price overstates the market's
# expected absolute move because the straddle also prices the tails; ~0.85x is the practitioner
# convention (e.g. a $14.00 straddle -> $11.90 expected move). Callers may override per config, but
# the default lives HERE so a recalibration reaches every module at once.
STRADDLE_TO_EM_FACTOR = 0.85


def expected_move(atm_call_mid: float, atm_put_mid: float, *, factor: float = STRADDLE_TO_EM_FACTOR) -> float:
    """The expected absolute move ($) implied by an ATM straddle's mid price, over the straddle
    expiration's horizon. The horizon is the caller's choice of chain: an earnings module feeds the
    post-event expiration, a weekly calendar module feeds its short-leg (front) expiration and reads
    the answer as the expected move to that Friday."""
    return factor * (atm_call_mid + atm_put_mid)


# Index-linked options (SPX/SPXW/XSP, and QQQ at these price levels) tick in nickels. A limit
# submitted off-tick is rejected, so every live order builder rounds -- and rounds TOWARD THE
# HOUSE: a credit floors (ask for a touch less, favours the fill), a debit ceils (offer a touch
# more, keeps a cushioned close marketable). Integer cents throughout so 1.07 -> 1.05 and never
# 1.0499999.
TICK = 0.05


def tick_floor(price: float, tick: float = TICK) -> float:
    """Round DOWN to the tick: the credit side of an order."""
    cents, t = int(round(price * 100)), int(round(tick * 100))
    return (cents // t) * t / 100.0


def tick_ceil(price: float, tick: float = TICK) -> float:
    """Round UP to the tick: the debit side of an order. Flooring a cushioned stop-close would
    undo the cushion, which is the whole point of meic's `stop_limit_ratio`."""
    cents, t = int(round(price * 100)), int(round(tick * 100))
    return -(-cents // t) * t / 100.0


def _wing(leg: Mapping, by_name: Mapping) -> Mapping | None:
    """The leg `leg` is a wing of: the one it is placed from by points, further out of the money (a
    put below, a call above) and on the same expiry. None when it is placed any other way."""
    rule = leg.get("strike") or {}
    ref = by_name.get(rule.get("leg")) if rule.get("rule") == "from_leg" else None
    points = rule.get("points") or 0
    if not ref or rule.get("pct") is not None or ref.get("right") != leg.get("right"):
        return None
    if (leg.get("expiry") or None) != (ref.get("expiry") or None):
        return None
    outward = points < 0 if leg.get("right") == "P" else points > 0
    return ref if outward else None


def entry_kind(arm: Mapping) -> str | None:
    """Whether the structure enters for a credit or a debit, from its rule alone: "credit" when every
    bought leg is a wing of a sold one (a condor, an iron fly, a credit vertical), "debit" when every
    sold leg is a wing of a bought one (a debit vertical), and None when the rule doesn't say (a
    calendar, a long butterfly), where either stop may fire."""
    legs = [g for g in arm.get("legs") or [] if isinstance(g.get("side"), int)]
    by_name = {g.get("name"): g for g in legs}
    sold = [g for g in legs if g["side"] < 0]
    bought = [g for g in legs if g["side"] > 0]
    if sold and not bought:
        return "credit"
    if bought and not sold:
        return "debit"

    def wings_of(those, side):
        return bool(those) and all(
            (ref := _wing(g, by_name)) is not None and ref["side"] * side < 0 for g in those
        )

    if wings_of(bought, 1):
        return "credit"
    if wings_of(sold, -1):
        return "debit"
    return None
