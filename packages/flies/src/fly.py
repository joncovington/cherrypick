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

# One fill model across the suite, not three: the fraction IS core's, structurally --
# not a literal kept in lockstep by comment. MEIC's paper.DEFAULT_SLIPPAGE_FRAC reads
# the same key.
DEFAULT_SLIPPAGE_FRAC = _fees.DEFAULT_COSTS["slippage_frac_of_spread"]

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


def fly_debit(
    lower_q: dict, center_q: dict, upper_q: dict, slippage_frac: float = DEFAULT_SLIPPAGE_FRAC
) -> float:
    """Debit paid buying a whole symmetric fly outright (+1 lower, -2 center, +1 upper), POSITIVE.

    Four contracts, so the haircut covers four leg-spreads: the centre leg is quoted once but traded
    twice, and paying slippage on only one of them would understate the cost of the leg that carries
    the most size.
    """
    mid = _leg_mid(lower_q) - 2 * _leg_mid(center_q) + _leg_mid(upper_q)
    spread = _leg_spread(lower_q) + 2 * _leg_spread(center_q) + _leg_spread(upper_q)
    return mid + slippage_frac * spread


def fly_close_credit(
    lower_q: dict, center_q: dict, upper_q: dict, slippage_frac: float = DEFAULT_SLIPPAGE_FRAC
) -> float:
    """Credit received CLOSING an existing fly ahead of expiry (sell lower, buy back the doubled
    centre, sell upper) — the mirror of `fly_debit`: selling nets LESS than mid by the same
    four-leg haircut, exactly as `vertical_credit` sits below `vertical_debit`. Used only to decide
    whether closing an ITM fly early is cheaper than the exercise-assignment fee it would otherwise
    incur (see `engine.evaluate_pre_close_exit`) — never to price a *held* position, which stays on
    intrinsic value (`fly_payoff`) until it actually trades.
    """
    mid = _leg_mid(lower_q) - 2 * _leg_mid(center_q) + _leg_mid(upper_q)
    spread = _leg_spread(lower_q) + 2 * _leg_spread(center_q) + _leg_spread(upper_q)
    return mid - slippage_frac * spread


# --------------------------------------------------------------------------- fees
def vertical_open_fee(symbol: str, quantity: int = 1) -> float:
    """Open a 2-leg vertical (1 sell leg). ndigits=4 so fees stay linear in quantity (MEIC parity)."""
    return _fees.ic_open_fee(symbol, quantity, legs=2, sell_legs=1, ndigits=4)


def vertical_close_fee(symbol: str, quantity: int = 1) -> float:
    """Close a 2-leg vertical ahead of expiry (buy back the short, sell the long -- still exactly
    1 sell-side contract either direction), at the schedule's closing commission rate."""
    return _fees.ic_close_fee(symbol, quantity, legs=2, sell_legs=1, ndigits=4)


def fly_open_fee(symbol: str, quantity: int = 1) -> float:
    """Open a fly outright. Priced as 4 contracts / 2 sell contracts: the middle strike trades twice
    and tastytrade fees per CONTRACT, not per price level, so the doubled centre must be counted."""
    return _fees.ic_open_fee(symbol, quantity, legs=4, sell_legs=2, ndigits=4)


def fly_close_fee(symbol: str, quantity: int = 1) -> float:
    """Close a fly ahead of expiry. Same 4-contract/2-sell-contract shape as opening one (buying
    back the doubled centre is 0 sell contracts, selling both wings is 2), at the schedule's
    (lower, often $0) closing commission rate rather than the opening one."""
    return _fees.ic_close_fee(symbol, quantity, legs=4, sell_legs=2, ndigits=4)


def expire_fee(itm_contracts: int = 0) -> float:
    """Cash-settled expiry: $0 per OTM leg (nothing to exercise), $5/contract for each of
    `itm_contracts` that finishes ITM and is exercised/assigned overnight — see
    `itm_contracts_at_settlement`. SPX/XSP only."""
    return _fees.ic_expire_fee(itm_contracts)


def itm_contracts_at_settlement(position: dict, settlement_price: float) -> int:
    """How many contracts across this position's legs finish strictly ITM at `settlement_price` —
    the count tastytrade's overnight $5/contract exercise-assignment fee is assessed on (charged
    the next business day, not at expiry itself). Exactly-at-the-strike is treated as OTM (no
    intrinsic value, nothing to exercise).

    A short vertical has 2 legs (short `center`, long the wing). A completed fly has 3 distinct
    strikes but 4 contracts — the centre carries 2 (one from the opening vertical, one from the
    completing vertical), and the fee is per CONTRACT, not per unique strike.
    """
    side, center, width = position["side"], position["center"], position["wing_width"]
    qty = position.get("quantity", 1)

    def _itm(strike: float) -> bool:
        return settlement_price < strike if side == PUT else settlement_price > strike

    kind = position["kind"]
    if kind == "fly":
        legs = [(center - width, 1), (center, 2), (center + width, 1)]
    elif kind == "short_vertical":
        long_strike = center - width if side == PUT else center + width
        legs = [(center, 1), (long_strike, 1)]
    else:
        raise ValueError(f"itm_contracts_at_settlement: unknown position kind {kind!r}")
    return sum(n for strike, n in legs if _itm(strike)) * qty


def assignment_fee(position: dict, settlement_price: float) -> float:
    """The overnight exercise-assignment fee this position would incur if it settled at
    `settlement_price` right now — $0 when every leg is OTM."""
    return expire_fee(itm_contracts_at_settlement(position, settlement_price))


# --------------------------------------------------------------------------- position accounting
def position_pnl(position: dict, underlying: float) -> float:
    """Dollar P&L of one position at an expiry price, net of its recorded fees AND the
    exercise-assignment fee this exact price would trigger (see `assignment_fee`).

    A position is a plain dict:
        kind        "fly" | "short_vertical"
        side        "put" | "call"          (ignored for `fly` payoff; kept for reporting)
        center      the fly's centre strike, or the vertical's SHORT strike
        wing_width  W
        net         per-contract cash so far: positive = credit taken in, negative = debit paid
        quantity    contracts
        fees        dollars already charged for this position
        status      when "settled", `fees` is trusted to ALREADY include the real assignment fee
                    (folded in once by `engine.settle()`) and none is added here again — every
                    other status (open, or absent, i.e. a hypothetical mark on a still-live
                    position) computes it fresh from `underlying`, so the payoff curve and the
                    session-timeline replay stay honest about a cost that has not happened yet
                    but would if the session ended at this price (honesty rule 1).
    """
    qty = position.get("quantity", 1)
    w = position["wing_width"]
    kind = position["kind"]
    if kind == "fly":
        payoff = fly_payoff(position["center"], w, underlying)
    elif kind == "short_vertical":
        payoff = short_vertical_payoff(position["side"], position["center"], w, underlying)
    else:
        raise ValueError(f"position_pnl: unknown position kind {kind!r}")
    cash = position["net"] + payoff
    fees = position.get("fees", 0.0)
    if position.get("status") != "settled":
        fees += assignment_fee(position, underlying)
    return cash * CONTRACT_MULTIPLIER * qty - fees


def position_floor(position: dict) -> float:
    """Worst-case dollar outcome of one position GOING FORWARD, net of fees — the honest
    "risk-free" number.

    A fly's payoff bottoms out at 0, so its floor is simply the cash already taken in less fees.
    That is a genuine per-position guarantee. A short vertical bottoms out at -W, full defined
    risk, and calling THAT risk-free would be the lie this module exists to avoid.

    Neither branch reserves the worst-case exercise-assignment fee. `engine.evaluate_pre_close_exit`
    closes ANY position (completed fly or still-open vertical) with an ITM leg ahead of expiry
    whenever doing so is cheaper than the assignment fee it would incur, so going forward the
    realistic worst case is bounded by whichever of (close now, hold to settlement) is cheaper —
    never more than the assignment fee itself, and often (this exit exists specifically because it
    usually is) less. This is NOT a claim the fee can never be paid: a broker/liquidity failure on
    the closing order would still fall back to the ordinary settlement path and the real assignment
    fee, a tail risk this floor deliberately does not reserve capital against, same as it does not
    reserve against the exit order itself failing to submit. `position_pnl`, unlike this function,
    still prices the fee fresh at every hypothetical price — this floor is the one place
    going-forward risk management gets to change what "worst case" means; the payoff curve and
    settle_now stay a pure expiry question.
    """
    qty = position.get("quantity", 1)
    kind = position["kind"]
    if kind == "fly":
        worst_payoff = 0.0
    elif kind == "short_vertical":
        worst_payoff = -position["wing_width"]
    else:
        raise ValueError(f"position_floor: unknown position kind {kind!r}")
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
    credit = sum(p["net"] * CONTRACT_MULTIPLIER * p.get("quantity", 1) for p in positions if p["net"] > 0)
    debits = sum(-p["net"] * CONTRACT_MULTIPLIER * p.get("quantity", 1) for p in positions if p["net"] < 0)
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
    through the strikes plus the flat regions beyond them sees every local minimum of the PAYOFF.

    The exercise-assignment fee is not linear, though — it is a step function that jumps by
    $5/contract the instant a leg crosses its strike (see `itm_contracts_at_settlement`), so the
    true worst dollar point sits an infinitesimal distance past a strike, not exactly on it. A
    strike itself is included as the OTM side of that step by convention (exactly-at-the-money has
    no intrinsic value to exercise), so a bare step grid would land the "worst" reading on the
    wrong side of every jump. `eps`-shifted neighbors on both sides of each strike fix that, to
    within a cent — negligible against a payoff scaled by CONTRACT_MULTIPLIER.
    """
    eps = 0.01
    strikes = []
    for p in positions:
        w = p["wing_width"]
        kind = p["kind"]
        if kind in ("fly", "short_vertical"):
            strikes += [p["center"] - w, p["center"], p["center"] + w]
        else:
            raise ValueError(f"_scan_prices: unknown position kind {kind!r}")
    lo, hi = min(strikes), max(strikes)
    pad = max(hi - lo, step * 4)
    prices, x = [], lo - pad
    while x <= hi + pad + 1e-9:
        prices.append(round(x, 4))
        x += step
    for s in strikes:
        prices.append(round(s - eps, 4))
        prices.append(round(s + eps, 4))
    return sorted(set(prices))


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
        band            (low, high) of the CONTIGUOUS non-negative zone containing the payoff
                        maximum, or None when the book is negative everywhere
        bands           every contiguous non-negative zone, low-to-high (the forest's zones)
        unbounded_below True when a short vertical leaves the book losing beyond its wings
    """
    if not positions:
        return {
            "worst": 0.0,
            "worst_at": None,
            "floor_holds": True,
            "band": None,
            "bands": [],
            "unbounded_below": False,
        }

    prices = _scan_prices(positions, step)
    pnls = [book_pnl(positions, x) for x in prices]
    worst = min(pnls)
    worst_at = prices[pnls.index(worst)]

    # Contiguous non-negative zones (runs on the grid). The strategy's own premise is a
    # FOREST -- several profit zones with troughs between them -- so a min/max over all
    # non-negative points would span a losing trough and claim the floor holds where it
    # doesn't. `band` is the zone containing the payoff maximum: a single honest range
    # that can understate coverage, never overstate it. The full set is in `bands`.
    zones: list[tuple[float, float]] = []
    run_start = run_end = None
    for x, v in zip(prices, pnls, strict=True):
        if v >= 0:
            if run_start is None:
                run_start = x
            run_end = x
        elif run_start is not None:
            zones.append((run_start, run_end))
            run_start = run_end = None
    if run_start is not None:
        zones.append((run_start, run_end))

    best_at = prices[pnls.index(max(pnls))]
    band = next((z for z in zones if z[0] <= best_at <= z[1]), None)

    # Beyond every strike the payoff is flat, so the endpoints of the grid are the true tails.
    unbounded = pnls[0] < 0 or pnls[-1] < 0
    return {
        "worst": round(worst, 2),
        "worst_at": worst_at,
        "floor_holds": worst >= 0,
        "band": band,
        "bands": zones,
        "unbounded_below": unbounded,
    }
