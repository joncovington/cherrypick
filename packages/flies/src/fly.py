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

from cherrypick.core import fees as _fees

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


def iron_fly_payoff(center: float, wing_width: float, underlying: float) -> float:
    """Per-contract expiry value of an IRON butterfly (short centre put + short centre call, long
    both wings) centered at `center` -- payoff-equivalent to `fly_payoff` shifted down by
    `wing_width`: bounded to [-wing_width, 0].

    Completing a legged position by selling the OPPOSITE-type credit spread (put held -> sell
    call, or vice versa) produces this shape instead of a single-type fly: the same intrinsic tent,
    but the two credits were collected instead of one credit funding a debit. Whether the combined
    position is genuinely risk-free depends entirely on whether the two credits summed exceed
    `wing_width` plus fees -- see `position_floor`'s iron_fly branch, which does NOT assume they did.
    """
    return fly_payoff(center, wing_width, underlying) - wing_width


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


def debit_vertical_payoff(side: str, center: float, wing_width: float, underlying: float) -> float:
    """Per-contract expiry value of the DEBIT vertical bought FIRST by the `debit_first` entry
    mode, as a signed number (>= 0, bounded to [0, wing_width]).

    A call fly's debit leg is +1 (K-w) call / -1 K call (bull call debit spread); a put fly's is
    +1 (K+w) put / -1 K put (bear put debit spread). Both are bought cheapest when spot sits on
    their OTM side and richen toward `wing_width` as spot moves through the centre -- the mirror
    image of `short_vertical_payoff`, whose worst case sits at -wing_width instead of this
    structure's best case of +wing_width. Do NOT confuse with `long_vertical_payoff`, which prices
    a different pair of strikes (the legged mode's COMPLETING debit spread, bought second, on the
    far side of an already-sold credit spread) -- this function is the debit-first mode's OPENING
    trade, priced against its own strikes.
    """
    if side == CALL:  # +1 (center-w) call / -1 center call
        return max(0.0, min(wing_width, underlying - (center - wing_width)))
    return max(0.0, min(wing_width, (center + wing_width) - underlying))  # +1 (center+w) put / -1 center put


def bwb_strikes(side: str, center: float, wing_width: float, far_width: float) -> tuple[float, float, float]:
    """(near_wing, center, far_wing) for a broken-wing butterfly: the near wing sits on the
    PROTECTED side at the usual `wing_width`, the far/wide wing sits on the RISK side at
    `far_width` (> wing_width) -- the extra room bought with the wider wing is what manufactures
    the entry credit, and it is also the real tail. PUT: near wing above centre (K+w), far wing
    below (K-f) -- protected upside, risk downside. CALL: mirrored.
    """
    if side == PUT:
        return center + wing_width, center, center - far_width
    return center - wing_width, center, center + far_width


def bwb_payoff(side: str, center: float, wing_width: float, far_width: float, underlying: float) -> float:
    """Per-contract expiry value of a broken-wing butterfly. Bounded to
    [wing_width - far_width, wing_width] -- unlike `fly_payoff`, the lower bound is NEGATIVE
    (far_width > wing_width by construction), the real tail risk this construction carries until
    the far wing is rolled in to match `wing_width` (see `engine.evaluate_roll`). Peaks at
    `wing_width` at the centre, same as a symmetric fly; ramps to 0 at the near wing exactly like
    a symmetric fly's near side; only the far side differs, extending past 0 down to the tail.
    """
    K, w, f, S = center, wing_width, far_width, underlying
    if side == PUT:  # +1 (K+w) put / -2 K put / +1 (K-f) put
        return max(0.0, (K + w) - S) - 2 * max(0.0, K - S) + max(0.0, (K - f) - S)
    return max(0.0, S - (K - w)) - 2 * max(0.0, S - K) + max(0.0, S - (K + f))  # +1 (K-w)/-2 K/+1 (K+f) call


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


def debit_first_completing_direction(side: str) -> str:
    """Which way spot must move for `debit_first`'s COMPLETING credit spread to richen -- the
    inverse of `completing_side_direction`. `choose_debit_side` picks CALL when spot starts at or
    below centre and PUT when spot starts above it, so either way the debit spread was bought on
    spot's current side of the centre; the completing credit spread (short at the centre) richens
    as spot moves TOWARD the centre (reversion), i.e. up from the CALL side, down from the PUT
    side -- opposite of legged's completion, which is exactly the point of offering both."""
    return "up" if side == CALL else "down"


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


# --------------------------------------------------------------------------- fees
def vertical_open_fee(symbol: str, quantity: int = 1) -> float:
    """Open a 2-leg vertical (1 sell leg). ndigits=4 so fees stay linear in quantity (MEIC parity)."""
    return _fees.ic_open_fee(symbol, quantity, legs=2, sell_legs=1, ndigits=4)


def fly_open_fee(symbol: str, quantity: int = 1) -> float:
    """Open a fly outright. Priced as 4 contracts / 2 sell contracts: the middle strike trades twice
    and tastytrade fees per CONTRACT, not per price level, so the doubled centre must be counted."""
    return _fees.ic_open_fee(symbol, quantity, legs=4, sell_legs=2, ndigits=4)


def expire_fee(itm_legs: int = 0) -> float:
    """Cash-settled expiry: $0 per OTM leg (nothing to exercise), $5 per SETTLEMENT EVENT for each
    of `itm_legs` — distinct ITM option symbols, not contracts — exercised/assigned overnight. See
    `itm_legs_at_settlement`. SPX/XSP only."""
    return _fees.ic_expire_fee(itm_legs)


def itm_legs_at_settlement(position: dict, settlement_price: float) -> int:
    """How many DISTINCT option symbols across this position's legs finish strictly ITM at
    `settlement_price` — the count tastytrade's overnight $5 exercise-assignment fee is assessed
    on (charged the next business day, not at expiry itself). Exactly-at-the-strike is treated as
    OTM (no intrinsic value, nothing to exercise).

    Per SETTLEMENT EVENT, not per contract, and NOT scaled by `quantity`. The broker settles one
    symbol as one transaction and charges it once however many contracts rest on it — corrected
    2026-07-31 against real fills (a 2-contract XSP put leg was charged $5.00, not $10.00; see
    `cherrypick.core.fees` for the transactions). So a completed fly has 3 distinct strikes and
    pays at most 3 events even though it holds 4 contracts: its doubled centre (one from the
    opening vertical, one from the completing one) is a single symbol and settles once.
    """
    center, width = position["center"], position["wing_width"]
    kind = position["kind"]

    if kind == "iron_fly":
        # Long (center-w) put, short center put, short center call, long (center+w) call -- this
        # geometry is the same regardless of which side was legged first (see engine's iron
        # completion), so `side` carries no information here, unlike every other kind (and isn't
        # even stored on an iron_fly row). Each option type has its own ITM direction, and all
        # four strikes are distinct symbols (two puts, two calls), so each counts once. Price
        # can't be below AND above the centre at once, so at most one side is ever ITM.
        put_legs = 2 if settlement_price < center - width else (1 if settlement_price < center else 0)
        call_legs = 2 if settlement_price > center + width else (1 if settlement_price > center else 0)
        return put_legs + call_legs

    side = position["side"]

    def _itm(strike: float) -> bool:
        return settlement_price < strike if side == PUT else settlement_price > strike

    if kind == "fly":
        strikes = [center - width, center, center + width]
    elif kind == "short_vertical":
        strikes = [center, center - width if side == PUT else center + width]
    elif kind == "long_vertical":
        # debit_first's opening trade: +1 (center-w) call / -1 center call (CALL side), or
        # +1 (center+w) put / -1 center put (PUT side) -- same two strikes as short_vertical's,
        # just held long instead of short.
        strikes = [center, center - width if side == CALL else center + width]
    elif kind == "bwb":
        # Single-type (all put or all call), so the same _itm test applies to every leg -- unlike
        # iron_fly's mixed-type geometry. Three distinct strikes like a symmetric fly (the doubled
        # centre is one symbol), just asymmetric spacing.
        near_wing, _, far_wing = bwb_strikes(side, center, width, position["far_width"])
        strikes = [near_wing, center, far_wing]
    else:
        raise ValueError(f"itm_legs_at_settlement: unknown position kind {kind!r}")
    return sum(1 for strike in strikes if _itm(strike))


def assignment_fee(position: dict, settlement_price: float) -> float:
    """The overnight exercise-assignment fee this position would incur if it settled at
    `settlement_price` right now — $0 when every leg is OTM."""
    return expire_fee(itm_legs_at_settlement(position, settlement_price))


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
    elif kind == "long_vertical":
        payoff = debit_vertical_payoff(position["side"], position["center"], w, underlying)
    elif kind == "iron_fly":
        payoff = iron_fly_payoff(position["center"], w, underlying)
    elif kind == "bwb":
        payoff = bwb_payoff(position["side"], position["center"], w, position["far_width"], underlying)
    else:
        raise ValueError(f"position_pnl: unknown position kind {kind!r}")
    cash = position["net"] + payoff
    fees = position.get("fees", 0.0)
    if position.get("status") != "settled":
        fees += assignment_fee(position, underlying)
    return cash * CONTRACT_MULTIPLIER * qty - fees


# Settlement events reserved by `position_floor`, per kind: the ITM-strike count at the price where
# the position's NET outcome is worst, not the largest count it could ever reach. Those are not the
# same price, so taking the maximum in isolation would over-reserve. Derived by walking each kind's
# payoff against `itm_legs_at_settlement`:
#
#   fly             payoff bottoms at 0 beyond either wing; past the far wing all 3 strikes are ITM
#   short_vertical  payoff bottoms at -W beyond the short wing, where both strikes are ITM
#   long_vertical   payoff bottoms at 0 BELOW the long strike, where NOTHING is ITM -- the binding
#                   point is just past the long strike (payoff still ~0, one strike ITM), so 1 is
#                   correct here and 2 would be wrong rather than merely conservative
#   iron_fly        payoff bottoms at -W beyond either wing; that side contributes 2 ITM strikes and
#                   the other side contributes 0 (price cannot be beyond both wings at once)
#   bwb             payoff bottoms at -(F-W) past the far wing, where all 3 strikes are ITM
#
# `tests/test_fly_math.py` pins every entry here against a brute-force price scan, so a future kind
# or geometry change cannot silently invalidate the table.
WORST_CASE_ITM_LEGS = {
    "fly": 3,
    "short_vertical": 2,
    "long_vertical": 1,
    "iron_fly": 2,
    "bwb": 3,
}


def position_floor(position: dict) -> float:
    """Worst-case dollar outcome of one position GOING FORWARD, net of fees — the honest
    "risk-free" number.

    A fly's payoff bottoms out at 0, so its floor is the cash already taken in less fees and the
    assignment fee it would owe there. That is a genuine per-position guarantee. A short vertical
    bottoms out at -W, full defined risk, and calling THAT risk-free would be the lie this module
    exists to avoid.

    Every kind reserves the exercise-assignment fee it would actually incur at its own worst-case
    settlement price — see `WORST_CASE_ITM_LEGS` above for the per-kind derivation. Before
    2026-08-01 the `fly`, `short_vertical`, and `iron_fly` branches reserved nothing, on the
    grounds that `engine.evaluate_pre_close_exit` would close any ITM position more cheaply than
    the fee. That exit has been removed (it lost ~$34/position in paper and never once fired in
    live — see CLAUDE.md rule 5), so the fee is now a cost the position genuinely carries to
    settlement and the floor reserves against it. A floor that assumes a mechanism which no longer
    exists is exactly the overstatement this module is built not to make.

    `position_pnl`, unlike this function, prices the fee fresh at every hypothetical price; this
    function asks only what the worst such price costs.
    """
    qty = position.get("quantity", 1)
    kind = position["kind"]
    if kind == "fly":
        worst_payoff = 0.0
    elif kind == "short_vertical":
        worst_payoff = -position["wing_width"]
    elif kind == "long_vertical":
        # A debit_first long vertical's worst payoff is genuinely 0 (it paid a debit; it cannot owe
        # more), reached below the long strike -- where nothing is ITM. The binding point is just
        # past the long strike: still ~0 payoff, but now one settlement event.
        worst_payoff = 0.0
    elif kind == "iron_fly":
        # Genuinely -wing_width, not 0: unlike a same-type fly, the two credits collected here are
        # not guaranteed to exceed the width, so this floor CAN be negative even before fees.
        worst_payoff = -position["wing_width"]
    elif kind == "bwb":
        # The tail past the far wing is a REAL loss, not a bounded-below payoff with a fee on top.
        worst_payoff = -(position["far_width"] - position["wing_width"])
    else:
        raise ValueError(f"position_floor: unknown position kind {kind!r}")
    reserve = expire_fee(WORST_CASE_ITM_LEGS[kind])
    return (position["net"] + worst_payoff) * CONTRACT_MULTIPLIER * qty - position.get("fees", 0.0) - reserve


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
    $5 the instant a leg crosses its strike (see `itm_legs_at_settlement`), so the
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
        if kind in ("fly", "short_vertical", "long_vertical", "iron_fly"):
            strikes += [p["center"] - w, p["center"], p["center"] + w]
        elif kind == "bwb":
            # Include the far strike -- book_floor must see the true trough, which sits past the
            # wide wing, not at +/-wing_width like every other kind.
            near_wing, _, far_wing = bwb_strikes(p["side"], p["center"], w, p["far_width"])
            strikes += [near_wing, p["center"], far_wing]
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
