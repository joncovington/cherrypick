"""Entry/completion decision engine for cherrypick-flies.

Pure functions over one pre-fetched snapshot, exactly like MEIC's `paper.py`: this module never
calls the broker, never reaches the network, and never asks a model anything. That is a suite
guardrail (no AI or network on a loop-decision path), and here it is also what makes the experiment
valid — the three arms are only comparable if the only thing that differs between them is the arm.

Two entry modes, both derived from real order chains:

  legged    Sell a defined-risk credit spread for credit C. Then, each iteration, price the spread
            that would COMPLETE it into a symmetric fly and buy it when the debit D is comfortably
            below C. The result is a butterfly held for a net credit of C - D — a position whose
            worst case at expiry is a profit. When D never gets low enough, nothing is bought and
            the credit spread simply runs to cash settlement carrying its ordinary defined risk.
            That second branch is expected to be the common one and is reported separately.

  outright  Buy a cheap fly for a debit, but only out of premium the book has already realized.
            This never manufactures a floor of its own; it spends one.

Three arms, differing only in WHERE and WHEN they centre a structure:

  gex           centre on the strongest positive per-strike net GEX near spot (the pin candidate)
  time_window   centre ATM, entering only inside configured time-of-day windows
  control       centre ATM at one fixed midday time — the naive baseline that makes the other two
                falsifiable. Without it, a profitable `gex` arm proves nothing about GEX.
"""

from __future__ import annotations

import fly

PUT, CALL = fly.PUT, fly.CALL
ARMS = ("gex", "time_window", "control")


# --------------------------------------------------------------------------- config
def merged_params(config: dict, arm: str) -> dict:
    """Base defaults overlaid with this arm's overrides. Arms are thin by design — an arm that
    redefined the gates as well as the centring would confound what the comparison measures."""
    params = dict(config.get("defaults", {}))
    params.update(config.get("arms", {}).get(arm, {}))
    params["arm"] = arm
    return params


def time_to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_entry_window(now_min: int | None, windows: list) -> tuple[bool, str | None]:
    """Is `now_min` (minute-of-day, ET) inside any configured window? Returns the window label too,
    because every trade is tagged with it — the per-window ranking has to emerge from our own
    sessions rather than from an assumption about which time of day is best."""
    if not windows:
        return True, None
    if now_min is None:
        return False, None
    for w in windows:
        start, end = time_to_minutes(w[0]), time_to_minutes(w[1])
        if start <= now_min <= end:
            return True, f"{w[0]}-{w[1]}"
    return False, None


# --------------------------------------------------------------------------- quotes
def quote(snapshot: dict, side: str, strike: float) -> dict | None:
    """Look up one leg quote. Strike keys are normalized because a snapshot round-tripped through
    JSON has string keys while one built in a test has floats."""
    book = snapshot.get("puts" if side == PUT else "calls") or {}
    for key, q in book.items():
        try:
            if abs(float(key) - float(strike)) < 1e-6:
                return q
        except (TypeError, ValueError):
            continue
    return None


def _have(snapshot: dict, side: str, strikes) -> bool:
    return all(quote(snapshot, side, s) is not None for s in strikes)


# --------------------------------------------------------------------------- centre selection
def atm_strike(spot: float, increment: float) -> float:
    return round(round(spot / increment) * increment, 4)


def select_center(snapshot: dict, params: dict) -> tuple[float | None, str]:
    """Pick the fly's centre strike for this arm. Returns (centre, reason).

    The `gex` arm degrades to ATM rather than skipping when GEX is unavailable, so a streamer that
    hasn't cached open interest yet costs us a signal, not a whole session of samples. The degrade is
    recorded in the reason string so those trades can be excluded from the arm's headline later.
    """
    spot = snapshot.get("underlying_price")
    increment = params.get("strike_increment", 5)
    if spot is None:
        return None, "no_underlying_price"

    if params.get("arm") != "gex":
        return atm_strike(spot, increment), "atm"

    gex = snapshot.get("gex") or {}
    per_strike = gex.get("per_strike") or []
    if not gex.get("ok") or not per_strike:
        return atm_strike(spot, increment), "atm_gex_unavailable"

    max_dist = params.get("max_center_distance_pct", 0.01) * spot
    near = [s for s in per_strike
            if s.get("net_gex", 0) > 0 and abs(s["strike"] - spot) <= max_dist]
    if not near:
        return atm_strike(spot, increment), "atm_no_positive_gex_near_spot"
    best = max(near, key=lambda s: s["net_gex"])
    return float(best["strike"]), "max_positive_gex"


def choose_side(snapshot: dict, center: float) -> str:
    """Which credit spread to sell first when legging in.

    Sell the side spot is already on the far end of, so the COMPLETING spread is the one that
    cheapens if the current drift continues. Spot below centre means the put spread is the one with
    room to work. This is a heuristic about which leg-in has a chance, not a directional view — the
    fly ends up symmetric either way.
    """
    spot = snapshot.get("underlying_price", center)
    return PUT if spot <= center else CALL


# --------------------------------------------------------------------------- legged entry (step 1)
def evaluate_credit_spread_entry(snapshot: dict, params: dict, open_positions: list) -> tuple:
    """Should this arm sell an opening credit spread? Returns (enter, reason, plan | None).

    `plan` carries everything the fill needs: side, centre (the SHORT strike, which becomes the fly's
    centre), wing width, the modeled credit, and the strike that would complete the fly later.
    """
    if snapshot.get("dte", 0) != 0:
        return False, "no_0dte_expiration", None

    ok_window, window = in_entry_window(snapshot.get("now_min"), params.get("entry_windows", []))
    if not ok_window:
        return False, "outside_entry_window", None

    if len(open_positions) >= params.get("max_positions", 4):
        return False, "max_positions_reached", None

    center, center_reason = select_center(snapshot, params)
    if center is None:
        return False, center_reason, None

    # One structure per centre: two flies on the same strike double the pin bet without adding a
    # profit zone, which is the opposite of what a "forest" of separate zones is for.
    if any(abs(p["center"] - center) < 1e-6 for p in open_positions):
        return False, "center_already_occupied", None

    width = params.get("wing_width", 5)
    side = choose_side(snapshot, center)
    long_strike = center - width if side == PUT else center + width
    if not _have(snapshot, side, [center, long_strike]):
        return False, "missing_leg_quotes", None

    slip = params.get("slippage_frac", fly.DEFAULT_SLIPPAGE_FRAC)
    credit = fly.vertical_credit(quote(snapshot, side, center), quote(snapshot, side, long_strike), slip)

    min_credit = params.get("min_credit_pct_of_width", 0.20) * width
    if credit < min_credit:
        return False, "credit_below_floor", None

    # A credit spread whose credit can't clear the fee stack on BOTH legs of the leg-in can never
    # produce a risk-free fly, so there is no reason to open it inside this strategy.
    symbol = snapshot["symbol"]
    qty = params.get("quantity", 1)
    round_trip_fees = fly.vertical_open_fee(symbol, qty) * 2
    if credit * fly.CONTRACT_MULTIPLIER * qty <= round_trip_fees:
        return False, "credit_cannot_clear_fees", None

    completing_strike = center + width if side == PUT else center - width
    return True, "ok", {
        "side": side,
        "center": center,
        "center_reason": center_reason,
        "wing_width": width,
        "credit": round(credit, 4),
        "quantity": qty,
        "open_fee": fly.vertical_open_fee(symbol, qty),
        "completing_strike": completing_strike,
        "completing_direction": fly.completing_side_direction(side),
        "entry_window": window,
    }


# --------------------------------------------------------------------------- legged entry (step 2)
def evaluate_completion(snapshot: dict, position: dict, params: dict) -> tuple:
    """Should this open credit spread be completed into a butterfly now? Returns (complete, reason, plan).

    The gate is `D < C - fee_buffer`, where the buffer must cover the second fee stack. Completing at
    D just under C would produce a fly with a positive gross credit and a negative floor after fees —
    the exact failure this module is built to expose rather than hide.
    """
    if position.get("kind") != "short_vertical":
        return False, "not_a_credit_spread", None

    side, center, width = position["side"], position["center"], position["wing_width"]
    long_strike = center + width if side == PUT else center - width
    if not _have(snapshot, side, [center, long_strike]):
        return False, "missing_leg_quotes", None

    slip = params.get("slippage_frac", fly.DEFAULT_SLIPPAGE_FRAC)
    # Buying the completing spread: long the far strike, short the centre (which offsets nothing —
    # it doubles the existing short into the fly's -2 centre).
    debit = fly.vertical_debit(quote(snapshot, side, long_strike), quote(snapshot, side, center), slip)

    symbol = snapshot["symbol"]
    qty = position.get("quantity", 1)
    completion_fee = fly.vertical_open_fee(symbol, qty)
    # fee_buffer is expressed in price points so it reads like every other threshold in the config;
    # the floor check below is the one that actually enforces solvency in dollars.
    buffer_pts = params.get("fee_buffer", 0.10)
    credit = position["net"]

    net = credit - debit
    floor = net * fly.CONTRACT_MULTIPLIER * qty - position.get("fees", 0.0) - completion_fee
    # Every return carries the priced debit, including the refusals. A refusal that discarded the
    # price would make "never completed" permanently ambiguous between "the market never offered it"
    # and "our buffer was too tight" -- and those call for opposite fixes. The caller records the
    # running minimum, which is what makes that question answerable after the fact.
    plan = {
        "debit": round(debit, 4),
        "net": round(net, 4),
        "completion_fee": completion_fee,
        "floor": round(floor, 2),
        "long_strike": long_strike,
        "gate_debit": round(credit - buffer_pts, 4),  # the debit this would have had to beat
    }

    if debit >= credit - buffer_pts:
        return False, "completing_debit_too_high", plan
    if floor < params.get("min_floor_dollars", 0.0):
        return False, "floor_below_minimum_after_fees", plan

    return True, "ok", plan


# --------------------------------------------------------------------------- outright entry
def evaluate_outright_entry(snapshot: dict, params: dict, open_positions: list,
                            realized_cash: float) -> tuple:
    """Should the book buy a cheap fly outright, funded by premium it has already realized?

    `realized_cash` is the book's credit-minus-debits-minus-fees so far. Requiring the debit to fit
    inside it is what keeps this mode honest: the book never spends money it hasn't taken in, so its
    floor is bounded by construction. That floor is still only BOOK-level and only holds inside the
    funding spreads' wings — `fly.book_floor` reports the band, and callers must not round it up to
    "risk-free".
    """
    if snapshot.get("dte", 0) != 0:
        return False, "no_0dte_expiration", None

    ok_window, window = in_entry_window(snapshot.get("now_min"), params.get("entry_windows", []))
    if not ok_window:
        return False, "outside_entry_window", None

    if len(open_positions) >= params.get("max_positions", 4):
        return False, "max_positions_reached", None

    center, center_reason = select_center(snapshot, params)
    if center is None:
        return False, center_reason, None
    if any(abs(p["center"] - center) < 1e-6 for p in open_positions):
        return False, "center_already_occupied", None

    width = params.get("wing_width", 5)
    side = CALL if snapshot.get("underlying_price", center) > center else PUT
    lower, upper = center - width, center + width
    if not _have(snapshot, side, [lower, center, upper]):
        return False, "missing_leg_quotes", None

    slip = params.get("slippage_frac", fly.DEFAULT_SLIPPAGE_FRAC)
    debit = fly.fly_debit(quote(snapshot, side, lower), quote(snapshot, side, center),
                          quote(snapshot, side, upper), slip)
    if debit <= 0:
        # A non-positive modeled debit means a stale or crossed quote, not free money: a long fly's
        # value is bounded below by zero, so nobody sells one for a credit.
        return False, "implausible_fly_quote", None

    symbol = snapshot["symbol"]
    qty = params.get("quantity", 1)
    open_fee = fly.fly_open_fee(symbol, qty)

    max_debit = params.get("max_fly_debit", 0.50)
    if debit > max_debit:
        return False, "fly_debit_above_max", None

    cost = debit * fly.CONTRACT_MULTIPLIER * qty + open_fee
    if cost > realized_cash:
        return False, "not_funded_by_realized_credit", None

    return True, "ok", {
        "side": side,
        "center": center,
        "center_reason": center_reason,
        "wing_width": width,
        "debit": round(debit, 4),
        "quantity": qty,
        "open_fee": open_fee,
        "cost": round(cost, 2),
        "entry_window": window,
    }


# --------------------------------------------------------------------------- settlement
def settle(positions: list[dict], settlement_price: float) -> list[dict]:
    """Cash-settle every open position at expiry. SPX/XSP are European cash-settled, so there is no
    assignment branch to model and no closing fee — the position simply resolves to intrinsic value.

    Deliberately there is no stop loss and no wing adjustment: once a structure exists it is held to
    settlement. v1 is measuring the base rate of this strategy, and an adjustment rule tuned before
    a single completion rate has been observed would be fitting noise.
    """
    out = []
    for p in positions:
        pnl = fly.position_pnl(p, settlement_price)
        out.append({
            **p,
            "settlement_price": settlement_price,
            "expiry_payoff": round(
                fly.fly_payoff(p["center"], p["wing_width"], settlement_price) if p["kind"] == "fly"
                else fly.short_vertical_payoff(p["side"], p["center"], p["wing_width"], settlement_price),
                4),
            "pnl": round(pnl, 2),
            "pinned": p["kind"] == "fly" and abs(settlement_price - p["center"]) < p["wing_width"],
            "status": "settled",
        })
    return out


def session_stats(positions: list[dict]) -> dict:
    """The three numbers the whole thesis turns on, per session.

    completion_rate  how often a credit spread actually became a fly. If this is near zero the
                     strategy is just short verticals wearing a costume.
    risk_free_rate   share of flies whose floor survived fees.
    pin_rate         share of flies that finished inside their wings (settled positions only).
    """
    flies = [p for p in positions if p["kind"] == "fly"]
    legged = [p for p in positions if p.get("entry_mode") == "legged"]
    legged_flies = [p for p in legged if p["kind"] == "fly"]
    settled_flies = [p for p in flies if p.get("status") == "settled"]

    def _rate(n, d):
        return round(n / d, 4) if d else None

    return {
        "positions": len(positions),
        "flies": len(flies),
        # Named for what it counts: structures that never became flies. This counts settled ones too,
        # because after the bell "still a vertical" is the outcome, not a transient state.
        "uncompleted_verticals": len([p for p in positions if p["kind"] == "short_vertical"]),
        "completion_rate": _rate(len(legged_flies), len(legged)),
        "risk_free_rate": _rate(len([p for p in flies if fly.is_risk_free(p)]), len(flies)),
        "pin_rate": _rate(len([p for p in settled_flies if p.get("pinned")]), len(settled_flies)),
    }
