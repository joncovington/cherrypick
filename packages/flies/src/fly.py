"""Butterfly structure, pricing, and floor math — the pure core of cherrypick-flies.

No broker, no network, no I/O: every function here takes an already-fetched snapshot (or plain
numbers) and returns numbers. Same discipline as MEIC's `paper.py` and `cherrypick.core.gex`.

Sign convention throughout — matching the tastytrade order chains this module was derived from:

    positive = credit received,  negative = debit paid

Verified against a real chain: `Avg Trd Pr -0.65`, `Mark 3.97`, `Total P/L 332.00`, and
(3.97 - 0.65) * 100 = 332. Get this backwards and every floor in the module flips sign.

The one fact the whole strategy rests on: a symmetric long butterfly pays `max(0, W - |S - K|)`,
which is bounded to `[0, W]` and is never negative. So a fly held for a NET CREDIT cannot lose at
expiry — its worst case is the credit itself. That is the "risk-free" claim, and `position_floor`
below is the measurement of it. Note that you cannot simply buy such a fly: paying a negative debit
for a non-negative payoff would be arbitrage. The credit has to be manufactured, which is what
`legged` entry does (see engine.py).
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.join(_HERE, "_core")
if os.path.isdir(_CORE) and _CORE not in sys.path:
    sys.path.insert(0, _CORE)

from cherrypick.core import fees as _fees  # noqa: E402

CONTRACT_MULTIPLIER = 100

# Keep in lockstep with cherrypick.core.fees.DEFAULT_COSTS["slippage_frac_of_spread"] and MEIC's
# paper.DEFAULT_SLIPPAGE_FRAC — one fill model across the suite, not three.
DEFAULT_SLIPPAGE_FRAC = 0.125

PUT, CALL = "put", "call"


# --------------------------------------------------------------------------- expiry payoffs
def fly_payoff(center: float, wing_width: float, underlying: float) -> float:
    """Per-contract expiry value of a LONG symmetric butterfly centered at `center`.

    Identical for a put fly and a call fly, which is why `side` is absent: at expiry only intrinsic
    value remains, and the two structures have the same intrinsic tent. Bounded to [0, wing_width].
    """
    return max(0.0, wing_width - abs(underlying - center))


def short_vertical_payoff(side: str, short_strike: float, wing_width: float, underlying: float) -> float:
    """Per-contract expiry value of a SHORT defined-risk vertical, as a signed number (<= 0).

    This is the branch the legged mode lands in when the completing spread never gets cheap enough:
    an ordinary credit spread carrying its full defined risk. Reporting it honestly is the point.
    """
    if side == PUT:  # short put spread: short `short_strike`, long `short_strike - wing_width`
        return -max(0.0, min(wing_width, short_strike - underlying))
    return -max(0.0, min(wing_width, underlying - short_strike))  # short call spread


def long_vertical_payoff(side: str, short_strike: float, wing_width: float, underlying: float) -> float:
    """Per-contract expiry value of the LONG (debit) vertical that completes a fly. Always >= 0.

    Completing a put fly centered at K means buying the K+W/K put debit spread; completing a call fly
    means buying the K-W/K call debit spread. Either way the long strike sits `wing_width` on the far
    side of the short one, so this is the mirror of `short_vertical_payoff`.
    """
    return -short_vertical_payoff(side, short_strike, wing_width, underlying)


# --------------------------------------------------------------------------- which way does it cheapen
def completing_side_direction(side: str) -> str:
    """Which way spot must move for the COMPLETING spread to get cheaper — 'up' or 'down'.

    This inverts by side and is the single easiest thing in the module to code backwards, so it gets
    its own named function and its own test. Both real legged flies in the reference book confirm it:

      - Put fly:  sold the K/K-W put spread, then bought the K+W/K put debit spread as price ROSE.
      - Call fly: sold the K/K+W call spread, then bought the K-W/K call debit spread as price FELL.

    In both cases the completing spread cheapens as spot moves AWAY from it — which is the same as
    moving away from the fly's center, in the direction of the credit spread already sold.
    """
    return "up" if side == PUT else "down"


# --------------------------------------------------------------------------- quote-level pricing
def _leg_mid(q: dict) -> float:
    m = q.get("mid")
    return m if m is not None else (q.get("bid", 0.0) + q.get("ask", 0.0)) / 2.0


def _leg_spread(q: dict) -> float:
    return max(q.get("ask", 0.0) - q.get("bid", 0.0), 0.0)


def vertical_credit(short_q: dict, long_q: dict, slippage_frac: float = DEFAULT_SLIPPAGE_FRAC) -> float:
    """Credit received SELLING a vertical: mid minus the slippage haircut. Mirrors MEIC `_open_credit`."""
    mid = _leg_mid(short_q) - _leg_mid(long_q)
    return mid - slippage_frac * (_leg_spread(short_q) + _leg_spread(long_q))


def vertical_debit(long_q: dict, short_q: dict, slippage_frac: float = DEFAULT_SLIPPAGE_FRAC) -> float:
    """Debit paid BUYING a vertical: mid plus the same haircut. Returned POSITIVE (a cost)."""
    mid = _leg_mid(long_q) - _leg_mid(short_q)
    return mid + slippage_frac * (_leg_spread(long_q) + _leg_spread(short_q))


def fly_debit(lower_q: dict, center_q: dict, upper_q: dict,
              slippage_frac: float = DEFAULT_SLIPPAGE_FRAC) -> float:
    """Debit paid buying a whole symmetric fly outright (+1 lower, -2 center, +1 upper), POSITIVE.

    Four contracts, so the haircut covers four leg-spreads: the centre leg is quoted once but traded
    twice, and paying slippage on only one of them would understate the cost of the leg that carries
    the most size.
    """
    mid = _leg_mid(lower_q) - 2 * _leg_mid(center_q) + _leg_mid(upper_q)
    spread = _leg_spread(lower_q) + 2 * _leg_spread(center_q) + _leg_spread(upper_q)
    return mid + slippage_frac * spread


# --------------------------------------------------------------------------- fees
def vertical_open_fee(symbol: str, quantity: int = 1) -> float:
    """Open a 2-leg vertical (1 sell leg). ndigits=4 so fees stay linear in quantity (MEIC parity)."""
    return _fees.ic_open_fee(symbol, quantity, legs=2, sell_legs=1, ndigits=4)


def fly_open_fee(symbol: str, quantity: int = 1) -> float:
    """Open a fly outright. Priced as 4 contracts / 2 sell contracts: the middle strike trades twice
    and tastytrade fees per CONTRACT, not per price level, so the doubled centre must be counted."""
    return _fees.ic_open_fee(symbol, quantity, legs=4, sell_legs=2, ndigits=4)


def expire_fee() -> float:
    """Cash-settled expiry costs nothing — no closing transaction. SPX/XSP only, by design."""
    return _fees.ic_expire_fee()


# --------------------------------------------------------------------------- position accounting
def position_pnl(position: dict, underlying: float) -> float:
    """Dollar P&L of one position at an expiry price, net of its recorded fees.

    A position is a plain dict:
        kind        "fly" | "short_vertical"
        side        "put" | "call"          (ignored for `fly` payoff; kept for reporting)
        center      the fly's centre strike, or the vertical's SHORT strike
        wing_width  W
        net         per-contract cash so far: positive = credit taken in, negative = debit paid
        quantity    contracts
        fees        dollars already charged for this position
    """
    qty = position.get("quantity", 1)
    w = position["wing_width"]
    if position["kind"] == "fly":
        payoff = fly_payoff(position["center"], w, underlying)
    else:
        payoff = short_vertical_payoff(position["side"], position["center"], w, underlying)
    cash = position["net"] + payoff
    return cash * CONTRACT_MULTIPLIER * qty - position.get("fees", 0.0)


def position_floor(position: dict) -> float:
    """Worst-case dollar outcome of one position at expiry, net of fees — the honest "risk-free" number.

    A fly's payoff bottoms out at 0, so its floor is simply the cash already taken in less fees. That
    is a genuine per-position guarantee. A short vertical bottoms out at -W, full defined risk, and
    calling THAT risk-free would be the lie this module exists to avoid.
    """
    qty = position.get("quantity", 1)
    worst_payoff = 0.0 if position["kind"] == "fly" else -position["wing_width"]
    return (position["net"] + worst_payoff) * CONTRACT_MULTIPLIER * qty - position.get("fees", 0.0)


def is_risk_free(position: dict) -> bool:
    """True when this position cannot lose money at expiry — floor >= 0 AFTER fees.

    Fees are the whole reason this is a function rather than `kind == "fly"`. A fly legged in for a
    $35 net credit against two 2-leg SPX fee stacks is NOT risk-free, and the module has to be able
    to say so.
    """
    return position_floor(position) >= 0.0


# --------------------------------------------------------------------------- book accounting
def book_pnl(positions: list[dict], underlying: float) -> float:
    return sum(position_pnl(p, underlying) for p in positions)


def book_cash(positions: list[dict]) -> dict:
    """Realized cash summary for a book: credit taken in, debits paid, fees, and the net of all three."""
    credit = sum(p["net"] * CONTRACT_MULTIPLIER * p.get("quantity", 1)
                 for p in positions if p["net"] > 0)
    debits = sum(-p["net"] * CONTRACT_MULTIPLIER * p.get("quantity", 1)
                 for p in positions if p["net"] < 0)
    fee_total = sum(p.get("fees", 0.0) for p in positions)
    return {
        "credit_collected": round(credit, 2),
        "debits_paid": round(debits, 2),
        "fees": round(fee_total, 2),
        "net_cash": round(credit - debits - fee_total, 2),
    }


def _scan_prices(positions: list[dict], step: float) -> list[float]:
    """Price grid spanning every position's payoff, padded a wing beyond the outermost strike.

    A book of flies and verticals is piecewise-linear with kinks only at strikes, so a grid stepping
    through the strikes plus the flat regions beyond them sees every local minimum.
    """
    strikes = []
    for p in positions:
        w = p["wing_width"]
        strikes += [p["center"] - w, p["center"], p["center"] + w]
    lo, hi = min(strikes), max(strikes)
    pad = max(hi - lo, step * 4)
    prices, x = [], lo - pad
    while x <= hi + pad + 1e-9:
        prices.append(round(x, 4))
        x += step
    return prices


def book_floor(positions: list[dict], step: float = 1.0) -> dict:
    """The book's worst-case P&L and the price band over which it stays non-negative.

    This is the honest form of the "risk-free / green everywhere" claim, and the distinction the
    module exists to enforce. A per-position floor (see `position_floor`) is unconditional. A BOOK
    floor that leans on open short verticals is only good WITHIN their wings — outside that band the
    book loses, no matter how green the middle of the risk graph looks.

    Returns:
        worst           minimum P&L found on the scan grid
        worst_at        the price where that minimum occurs
        floor_holds     True when the book is non-negative EVERYWHERE (unconditionally risk-free)
        band            (low, high) contiguous range around spot-of-max where P&L >= 0, or None
        unbounded_below True when a short vertical leaves the book losing beyond its wings
    """
    if not positions:
        return {"worst": 0.0, "worst_at": None, "floor_holds": True, "band": None,
                "unbounded_below": False}

    prices = _scan_prices(positions, step)
    pnls = [book_pnl(positions, x) for x in prices]
    worst = min(pnls)
    worst_at = prices[pnls.index(worst)]

    positive = [x for x, v in zip(prices, pnls, strict=False) if v >= 0]
    band = (min(positive), max(positive)) if positive else None

    # Beyond every strike the payoff is flat, so the endpoints of the grid are the true tails.
    unbounded = pnls[0] < 0 or pnls[-1] < 0
    return {
        "worst": round(worst, 2),
        "worst_at": worst_at,
        "floor_holds": worst >= 0,
        "band": band,
        "unbounded_below": unbounded,
    }
