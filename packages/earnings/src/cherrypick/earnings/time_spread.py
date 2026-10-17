"""Earnings time spreads: the modelled endurable gap, coverage, and the side ladder.

The engine behind docs/time-spread-screen-plan.md. Pure functions over numbers the caller already
holds -- an entry chain's leg prices and IVs, the name's past reaction gaps -- with no I/O, so a replay
over old chains and a live scan compute the same thing. Nothing here opens, sizes or closes a
position, and no strategy reads it yet: the history study decides whether one should.

**The model.** Both expiries of a time spread hold the same announcement. Each one's total variance
is a base vol over its life plus the one event's variance (the Dubinsky-Johannes two-expiry split),
so the two ATM IVs give both. After the report the event variance is gone from every leg; each leg
keeps the variance its own entry IV held beyond the event (sticky strike, so the smile carries over),
and is repriced with Black-Scholes at the gapped spot, the hold's days later. European pricing: early
exercise is ignored, as everywhere else in the suite's replays, so a deep in-the-money short is
priced, not assigned.

**Units.** Gaps are signed fractions of the pre-report spot (+0.04 is a 4% rise). Vols are annual,
days are calendar days over 365. Money is whole-position dollars, the suite's convention; costs are
the caller's (`cherrypick.core.fees`), passed in as one round-trip figure, so this module never
guesses a fee schedule.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import erf, exp, log, sqrt
from statistics import median

MULTIPLIER = 100
YEAR_DAYS = 365.0

# A leg whose entry IV cannot hold the event's variance (a cheap wing under a rich ATM) is floored
# here rather than given a zero or imaginary vol. Counted on every result, never silent.
MIN_VOL = 0.05

# The endurable gap is walked out from zero in steps this size, then refined by bisection.
GAP_STEP = 0.0025
MAX_GAP = 0.5
_BISECT_ROUNDS = 40


@dataclass(frozen=True)
class Leg:
    """One leg at entry. `qty` is signed: positive long, negative short."""

    qty: int
    option_type: str  # "C" or "P"
    strike: float
    days: float  # calendar days to expiry at entry
    price: float  # entry price per share (the mid, or the fill)
    iv: float  # entry IV, annual


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def bs_price(
    option_type: str, spot: float, strike: float, years: float, vol: float, rate: float = 0.0
) -> float:
    """Black-Scholes value per share. Intrinsic at or past expiry, or with no vol."""
    intrinsic = max(spot - strike, 0.0) if option_type == "C" else max(strike - spot, 0.0)
    if years <= 0 or vol <= 0:
        return intrinsic
    sd = vol * sqrt(years)
    d1 = (log(spot / strike) + (rate + 0.5 * vol * vol) * years) / sd
    d2 = d1 - sd
    disc = exp(-rate * years)
    if option_type == "C":
        return spot * norm_cdf(d1) - strike * disc * norm_cdf(d2)
    return strike * disc * norm_cdf(-d2) - spot * norm_cdf(-d1)


def event_variance(front_iv: float, front_days: float, back_iv: float, back_days: float) -> dict:
    """Split two ATM IVs into a base vol and one event's variance.

    w = iv^2 * t is each expiry's total variance; with one shared base vol b and event variance e,
    w_front = b^2 t_front + e and w_back = b^2 t_back + e. Two equations, two unknowns. Refused when
    the term structure cannot hold an event: the back's total variance not above the front's (no
    base vol is left), or the front no richer than the base implies (no event premium to sell).
    `event_move` is the one-day jump's standard deviation as a fraction of spot -- not the suite's
    expected move, which is 0.85 x the front straddle.
    """
    t1, t2 = front_days / YEAR_DAYS, back_days / YEAR_DAYS
    if not 0 < t1 < t2 or front_iv <= 0 or back_iv <= 0:
        return {"ok": False, "reason": "bad_inputs"}
    w1, w2 = front_iv * front_iv * t1, back_iv * back_iv * t2
    base_var = (w2 - w1) / (t2 - t1)
    if base_var <= 0:
        return {"ok": False, "reason": "back_variance_not_above_front"}
    event = w1 - base_var * t1
    if event <= 0:
        return {"ok": False, "reason": "no_event_premium", "base_vol": sqrt(base_var)}
    return {"ok": True, "base_vol": sqrt(base_var), "event_variance": event, "event_move": sqrt(event)}


def post_event_vol(leg: Leg, event_var: float, base_shock: float = 0.0) -> tuple[float, bool]:
    """A leg's IV once the event has passed, and whether it hit the floor.

    The leg's entry total variance less the event's, spread back over its life: what its own strike's
    IV held beyond the event. `base_shock` scales the result (-0.1 is base vol falling 10% after the
    report) -- the back-leg risk the screen measures rather than assumes.
    """
    t = leg.days / YEAR_DAYS
    rest = leg.iv * leg.iv * t - event_var
    clamped = rest <= MIN_VOL * MIN_VOL * t
    vol = MIN_VOL if clamped else sqrt(rest / t)
    return vol * (1.0 + base_shock), clamped


def position_pnl(
    legs: Iterable[Leg],
    spot: float,
    gap: float,
    event_var: float,
    *,
    hold_days: float = 1.0,
    base_shock: float = 0.0,
    costs: float = 0.0,
    rate: float = 0.0,
) -> float:
    """Whole-position dollars at the exit after a gap: each leg repriced at spot x (1 + gap), its
    post-event vol and `hold_days` less to run, against its entry price, less `costs`."""
    moved = spot * (1.0 + gap)
    total = 0.0
    for leg in legs:
        vol, _ = post_event_vol(leg, event_var, base_shock)
        years = max(leg.days - hold_days, 0.0) / YEAR_DAYS
        total += leg.qty * (bs_price(leg.option_type, moved, leg.strike, years, vol, rate) - leg.price)
    return total * MULTIPLIER - costs


def clamped_legs(legs: Iterable[Leg], event_var: float) -> int:
    return sum(1 for leg in legs if post_event_vol(leg, event_var)[1])


def endurable_gap(
    legs: list[Leg],
    spot: float,
    event_var: float,
    *,
    loss_line: float = 0.0,
    max_gap: float = MAX_GAP,
    **model,
) -> dict:
    """The largest fall and rise, from an unchanged stock, over which P&L stays at or above
    `loss_line` (dollars: 0 is break-even, -25.0 a tolerated $25 loss).

    Walked outward from zero, so it is the interval that holds the unchanged stock. A structure that
    loses with the stock unchanged (a far OTM calendar) has no such interval and is refused here;
    `coverage` still scores it correctly, since it reads P&L at each past gap directly. A side that
    never crosses within `max_gap` is reported at the cap and flagged.
    """

    def pnl(g: float) -> float:
        return position_pnl(legs, spot, g, event_var, **model)

    unchanged = pnl(0.0)
    if unchanged < loss_line:
        return {"ok": False, "reason": "loses_unchanged", "pnl_unchanged": unchanged}

    out = {"ok": True, "pnl_unchanged": unchanged}
    for name, sign in (("down", -1.0), ("up", 1.0)):
        good, bad = 0.0, None
        g = GAP_STEP
        while g <= min(max_gap, 0.99) + 1e-12:  # a fall of 100% is no price at all
            if pnl(sign * g) < loss_line:
                bad = g
                break
            good = g
            g += GAP_STEP
        if bad is None:
            out[name], out[f"{name}_capped"] = good, True
            continue
        for _ in range(_BISECT_ROUNDS):
            mid = 0.5 * (good + bad)
            if pnl(sign * mid) < loss_line:
                bad = mid
            else:
                good = mid
        out[name], out[f"{name}_capped"] = good, False
    return out


def coverage(
    legs: list[Leg],
    spot: float,
    event_var: float,
    past_gaps: Iterable[float],
    *,
    loss_line: float = 0.0,
    **model,
) -> dict:
    """Share of the name's past signed reaction gaps this position would have come through at or
    above `loss_line`. Each past gap is applied to today's position, as a percentage of today's spot.
    """
    gaps = [g for g in past_gaps if g is not None]
    if not gaps:
        return {"n": 0, "covered": 0, "coverage": None}
    covered = sum(1 for g in gaps if position_pnl(legs, spot, g, event_var, **model) >= loss_line)
    return {"n": len(gaps), "covered": covered, "coverage": covered / len(gaps)}


def screen_scores(
    legs: list[Leg],
    spot: float,
    *,
    front_atm_iv: float,
    front_days: float,
    back_atm_iv: float,
    back_days: float,
    past_gaps: Iterable[float],
    loss_line: float = 0.0,
    **model,
) -> dict:
    """The plan's core scores for one candidate: coverage, cushion and richness, with the endurable
    gap and the event split they rest on. Refused (ok False, with the reason) when the term structure
    holds no event; every number the scores need is on the result, so a refusal can be read back."""
    split = event_variance(front_atm_iv, front_days, back_atm_iv, back_days)
    if not split["ok"]:
        return {"ok": False, "reason": split["reason"], "split": split}
    ev = split["event_variance"]
    gaps = [g for g in past_gaps if g is not None]
    gap = endurable_gap(legs, spot, ev, loss_line=loss_line, **model)
    cov = coverage(legs, spot, ev, gaps, loss_line=loss_line, **model)
    implied = split["event_move"]
    typical = median(abs(g) for g in gaps) if gaps else None
    return {
        "ok": True,
        "split": split,
        "endurable": gap,
        "coverage": cov,
        "cushion": (min(gap["down"], gap["up"]) / implied) if gap["ok"] else None,
        "richness": (implied / typical) if typical else None,
        "clamped_legs": clamped_legs(legs, ev),
    }


# --- choosing the side and the structure -------------------------------------------------------

FAMILIES = ("price", "positioning", "fundamentals")
RUNGS = ("neutral", "lean", "side", "diagonal")


def vote(value: float | None, low_cut: float, high_cut: float, sign: int = 1) -> int:
    """+1 above the high cut, -1 below the low cut, 0 between or unmeasured. `sign` -1 reads the
    signal contrarian. The cuts and the sign are fitted on the training years, never chosen here."""
    if value is None:
        return 0
    if value > high_cut:
        return sign
    if value < low_cut:
        return -sign
    return 0


def family_vote(member_votes: Iterable[int]) -> int:
    """One vote for a family from its members' votes: the sign of their sum, 0 on a tie or none.
    Members of one family share their inputs, so they are one opinion, never several."""
    total = sum(member_votes)
    return (total > 0) - (total < 0)


def side_rung(votes: Mapping[str, int]) -> dict:
    """The rung of the ladder the family votes reach, and the side.

    Any disagreement is neutral: a price read never overrides the options market's own skew, or the
    reverse. Otherwise the number of families agreeing climbs the ladder, one rung each.
    """
    nonzero = [v for v in votes.values() if v]
    if not nonzero or (1 in nonzero and -1 in nonzero):
        return {"rung": "neutral", "side": 0, "agreeing": 0}
    n = min(len(nonzero), len(RUNGS) - 1)
    return {"rung": RUNGS[n], "side": nonzero[0], "agreeing": len(nonzero)}


def structure_for(rung: str, *, diagonal_clears: bool, lean_as: str = "neutral") -> str:
    """The structure a rung trades. The diagonal needs its own endurable gap to clear both ways
    (`diagonal_clears`), and otherwise steps down to the OTM calendar. What one family alone buys
    is the plan's open question, so `lean_as` names it: "neutral" or "otm_calendar"."""
    if rung == "neutral":
        return "neutral"
    if rung == "lean":
        return lean_as
    if rung == "side":
        return "otm_calendar"
    if rung == "diagonal":
        return "diagonal" if diagonal_clears else "otm_calendar"
    raise ValueError(f"unknown rung {rung!r}")
