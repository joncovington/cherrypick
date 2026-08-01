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
# `wide_wing` is control's twin — ATM, same window — differing only in `wing_width`, so the pair
# isolates wing width the way gex vs control isolates centring. It exists because completions arrive
# only after spot has walked away from the centre: median drift to completion was 15.3-17.3 points
# against a 5-point wing over 07-20..07-24, so 19 of 23 completed flies settled outside their wings.
# The mechanism that makes a completion cheap is the one that puts the peak out of reach, and a wing
# that brackets the observed drift is the obvious test of whether that is fixable or fundamental.
#
# The `width-N` arms (2026-07-29, with the XSP move) generalize that single hypothesis into a sweep:
# each is another control twin pinning `wing_width` to N strike increments, so the wing question is
# answered as a curve rather than one point. There is no `width-1` arm — `control` at the default
# width IS the 1-increment rung, and a duplicate ATM book under a second name would double-count it.
# `wide_wing` stays for the SPX-era books' attribution but is disabled in config on XSP, where its
# 20-point wing is off-scale (the scaled equivalent of the drift it brackets is covered by width-2).
ARMS = (
    "gex",
    "time_window",
    "control",
    "wide_wing",
    "width-2",
    "width-3",
    "width-4",
    "width-5",
    "debit-first",
    "iron",
    "bwb",
)


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

    # Centre on TOTAL gamma (call + put), not net GEX.
    #
    # Pinning is caused by dealer gamma CONCENTRATION: a strike where dealers hold a large hedged
    # position drags price toward it as they trade against every move, and that pull does not care
    # which side the gamma sits on. Net GEX (call - put) measures directional positioning instead,
    # and it nets a strike with huge call AND huge put gamma — the hardest-pinning kind — to roughly
    # zero.
    #
    # Measured against a real SPX chain: net GEX was negative at EVERY strike within +/-40 points of
    # spot, because put open interest dominates there. "Max positive net GEX near spot" therefore had
    # no candidate to find near spot at all, and could only reach strikes ~67 points away where calls
    # finally take over — far enough that the structure stops being a pin bet. Total gamma on the
    # same chain peaked at -8, +12 and +32 points, exactly the range a 0DTE pin bet wants.

    # Deliberately tight. This is a 0DTE PIN bet: a centre far from spot needs a large move in the
    # remaining hours just to reach the peak, which is the opposite of the thesis. At 0.003 on a
    # ~7500 index this is about +/-22 points, roughly four strikes on the 5-point grid.
    #
    # It was 0.01, which allowed +/-75 points at that index level and let the gex arm centre on a
    # GEX wall 67 points away — GEX walls sit away from spot by nature, so the loose cap bit exactly
    # where the arm was most active. Trade-off worth watching: too tight and the gex arm collapses
    # onto ATM, making it indistinguishable from `control`. `analytics.arm_divergence` is the
    # instrument for that — if agreement runs near 100%, this is the first number to revisit.
    max_dist = params.get("max_center_distance_pct", 0.003) * spot

    def total_gamma(entry):
        return abs(entry.get("call_gex", 0.0)) + abs(entry.get("put_gex", 0.0))

    near = [s for s in per_strike if total_gamma(s) > 0 and abs(s["strike"] - spot) <= max_dist]
    if not near:
        return atm_strike(spot, increment), "atm_no_gamma_near_spot"
    best = max(near, key=total_gamma)
    return float(best["strike"]), "max_total_gamma"


# --------------------------------------------------------------------------- regime tagging
def classify_regime(snapshot: dict, params: dict) -> dict:
    """Tag the current market state along four dimensions, each a pure read of the snapshot
    already in hand -- no cross-tick state, no new data source, same no-I/O discipline as every
    other function here. Recorded (not acted on) at entry and completion, so the eventual
    question this exists to answer -- "which entry/completion mode wins under which regime" --
    has real, regime-labelled outcomes to be answered from once enough sessions accumulate.
    Deliberately does NOT include a trend/chop read: that needs a reference point in time (spot
    now vs. spot N minutes ago) that no single snapshot carries, and guessing at that plumbing
    before there is a reason to is the mistake this module's honesty rules exist to prevent.

    Returns {"vol_bucket", "gex_bucket", "time_bucket", "skew_bucket"} -- four independent values,
    not one collapsed string, so analytics can slice on any one dimension or their cross product.
    Every threshold below is a placeholder pending recalibration once real sessions accumulate,
    flagged the same way in config.example.json as every other gate in this module.
    """
    return {
        "vol_bucket": _classify_vol(snapshot, params),
        "gex_bucket": _classify_gex(snapshot, params),
        "time_bucket": _classify_time(snapshot, params),
        "skew_bucket": _classify_skew(snapshot, params),
    }


def _classify_vol(snapshot: dict, params: dict) -> str:
    """ATM straddle price / spot -- a cheap 0DTE expected-move proxy. No IV surface is available
    here, so this reads the market's own pricing of the straddle directly rather than backing out
    an implied vol number the snapshot has no inputs to compute honestly."""
    spot = snapshot.get("underlying_price")
    if spot is None or spot <= 0:
        return "unknown"
    strike = atm_strike(spot, params.get("strike_increment", 5))
    put_q, call_q = quote(snapshot, PUT, strike), quote(snapshot, CALL, strike)
    if put_q is None or call_q is None:
        return "unknown"
    straddle = fly._leg_mid(put_q) + fly._leg_mid(call_q)
    ratio = straddle / spot
    if ratio < params.get("regime_vol_low_pct", 0.0015):
        return "low"
    if ratio > params.get("regime_vol_high_pct", 0.0035):
        return "high"
    return "normal"


def _classify_gex(snapshot: dict, params: dict) -> str:
    """Reuses the `gex` arm's own per-strike concentration read (not arm-gated -- every arm can
    tag the regime it traded in, not just the one that trades on it). "unknown" whenever the OI
    cache the streamer would need isn't populated yet, mirroring `select_center`'s own honest
    degrade -- never guessed."""
    gex = snapshot.get("gex") or {}
    per_strike = gex.get("per_strike") or []
    if not gex.get("ok") or not per_strike:
        return "unknown"
    totals = [abs(s.get("call_gex", 0) + s.get("put_gex", 0)) for s in per_strike]
    total_sum = sum(totals)
    if total_sum <= 0:
        return "unknown"
    share = max(totals) / total_sum
    return "pinning" if share >= params.get("regime_gex_pinning_share", 0.5) else "thin"


def _classify_time(snapshot: dict, params: dict) -> str:
    now_min = snapshot.get("now_min")
    if now_min is None:
        return "unknown"
    open_end = time_to_minutes(params.get("regime_time_open_end", "10:00"))
    close_start = time_to_minutes(params.get("regime_time_close_start", "15:30"))
    if now_min < open_end:
        return "open"
    if now_min >= close_start:
        return "close"
    return "midday"


def _classify_skew(snapshot: dict, params: dict) -> str:
    """Reads directional skew straight out of the chain already in hand: compares the OTM put at
    `center - wing_width` against the OTM call at `center + wing_width` -- the exact strikes this
    module already trades, not an arbitrary distance. A richer put than its equidistant call means
    the market is pricing more downside risk than upside, and vice versa."""
    spot = snapshot.get("underlying_price")
    if spot is None:
        return "unknown"
    center = atm_strike(spot, params.get("strike_increment", 5))
    width = params.get("wing_width", 5)
    put_q = quote(snapshot, PUT, center - width)
    call_q = quote(snapshot, CALL, center + width)
    if put_q is None or call_q is None:
        return "unknown"
    put_mid, call_mid = fly._leg_mid(put_q), fly._leg_mid(call_q)
    avg = (put_mid + call_mid) / 2.0
    if avg <= 0:
        return "unknown"
    diff = (put_mid - call_mid) / avg
    threshold = params.get("regime_skew_threshold", 0.15)
    if diff > threshold:
        return "put_skew"
    if diff < -threshold:
        return "call_skew"
    return "flat"


def choose_side(snapshot: dict, center: float) -> str:
    """Which credit spread to sell first when legging in.

    Sell the side spot is already on the far end of, so the COMPLETING spread is the one that
    cheapens if the current drift continues. Spot below centre means the put spread is the one with
    room to work. This is a heuristic about which leg-in has a chance, not a directional view — the
    fly ends up symmetric either way.
    """
    spot = snapshot.get("underlying_price", center)
    return PUT if spot <= center else CALL


def before_open_gate(params: dict, now_min: int | None) -> bool:
    """Is it still inside the post-open blackout? A floor that an arm's own windows cannot override.

    Deliberately NOT expressed as "just set every arm's first window later". Each arm carries its own
    `entry_windows`, so four separate lists are four chances to silently reopen the hole when an arm is
    added or edited — and MEIC is the worked example of what config-only enforcement looks like when it
    fails there (its `entry_window_start` said 10:00 while the paper engine, which only applied the
    check for opt-in profiles, traded from 09:30 for the entire dataset). This gate is checked before
    the window logic and answers to `no_entry_before` alone, so an arm asking for an earlier window
    gets refused rather than obeyed.

    Off when unset, so nothing changes for a config that has not opted in.
    """
    floor = params.get("no_entry_before")
    if not floor or now_min is None:
        return False
    return now_min < time_to_minutes(floor)


def _window_cap_reached(params: dict, open_positions: list, window: str | None) -> bool:
    """Has this entry window already used up its own share of the position budget?

    A global `max_positions` alone does not make a multi-window arm test its windows: the book fills
    in the first window and the later ones are never reached. Over 07-20..07-24 the time_window arm
    put 15 of its 16 legged entries in 10:30-11:00, 1 in 12:30-13:00 and 0 in 14:00-14:30 — so the
    timing hypothesis the arm exists to test was never actually exercised, and the per-window ranking
    the config asks for had nothing to rank. It is the same failure the arm's `_history_note` already
    records once, and a global cap cannot prevent it.

    Off unless `max_positions_per_window` is configured, so single-window arms and the existing
    behaviour are untouched. Positions entered before the cap existed carry no window and are simply
    not counted against one.
    """
    cap = params.get("max_positions_per_window")
    if cap is None or window is None:
        return False
    return sum(1 for p in open_positions if p.get("entry_window") == window) >= cap


# --------------------------------------------------------------------------- legged entry (step 1)
def evaluate_credit_spread_entry(snapshot: dict, params: dict, open_positions: list) -> tuple:
    """Should this arm sell an opening credit spread? Returns (enter, reason, plan | None).

    `plan` carries everything the fill needs: side, centre (the SHORT strike, which becomes the fly's
    centre), wing width, the modeled credit, and the strike that would complete the fly later.
    """
    if snapshot.get("dte", 0) != 0:
        return False, "no_0dte_expiration", None

    # Checked BEFORE the arm's own windows so an arm cannot configure its way past the blackout.
    if before_open_gate(params, snapshot.get("now_min")):
        return False, "before_open_gate", None

    ok_window, window = in_entry_window(snapshot.get("now_min"), params.get("entry_windows", []))
    if not ok_window:
        return False, "outside_entry_window", None

    if len(open_positions) >= params.get("max_positions", 4):
        return False, "max_positions_reached", None

    if _window_cap_reached(params, open_positions, window):
        return False, "max_positions_this_window_reached", None

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

    # Ceiling as well as floor. A defined-risk vertical cannot be worth more than its width, so a
    # credit approaching that width means the short leg is deep in the money and the "premium" is
    # mostly intrinsic. Selling one of those is a low-probability directional bet, not a pin bet.
    #
    # Found against real SPX data: the gex arm centred 67 points from spot and produced a short
    # 7525/7520 put spread with spot at 7457 — 77% of width in credit, 67 points of intrinsic,
    # profitable only on a 67-point rally. `min_credit_pct_of_width` cannot catch this, because a
    # fatter intrinsic-heavy credit looks BETTER to a floor. Hence a separate ceiling, applied to
    # every arm rather than just the one that exposed it.
    max_credit = params.get("max_credit_pct_of_width", 0.60) * width
    if credit > max_credit:
        return False, "credit_above_ceiling_mostly_intrinsic", None

    # A credit spread whose credit can't clear the fee stack on BOTH legs of the leg-in can never
    # produce a risk-free fly, so there is no reason to open it inside this strategy.
    symbol = snapshot["symbol"]
    qty = params.get("quantity", 1)
    round_trip_fees = fly.vertical_open_fee(symbol, qty) * 2
    if credit * fly.CONTRACT_MULTIPLIER * qty <= round_trip_fees:
        return False, "credit_cannot_clear_fees", None

    completing_strike = center + width if side == PUT else center - width
    return (
        True,
        "ok",
        {
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
        },
    )


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
    completed_fees = position.get("fees", 0.0) + completion_fee
    # Reuse fly.position_floor rather than a second, duplicate formula: it already carries the
    # worst-case exercise-assignment fee (4 contracts for a fly, see position_floor's own
    # docstring), which a completion's own inline floor formerly omitted entirely -- a completion
    # could clear the dollar gate on a floor that only looked non-negative because it never priced
    # in the one cost every ITM leg of the resulting fly can actually incur.
    floor = fly.position_floor(
        {
            "kind": "fly",
            "side": side,
            "center": center,
            "wing_width": width,
            "net": net,
            "quantity": qty,
            "fees": completed_fees,
        }
    )
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


# --------------------------------------------------------------------------- debit-first entry (step 1)
def choose_debit_side(snapshot: dict, center: float) -> str:
    """Which debit vertical to buy first when legging in via `debit_first` -- the inverse of
    `choose_side`. Buy the side spot is currently on the OTM end of, so the debit is cheap now and
    has room to richen as spot moves toward the centre -- the same direction the completing credit
    spread richens in (see `fly.debit_first_completing_direction`). `choose_side` instead sells the
    side spot has already crossed, betting on the COMPLETING spread cheapening on continued drift
    away -- the opposite regime.
    """
    spot = snapshot.get("underlying_price", center)
    return CALL if spot <= center else PUT


def evaluate_debit_vertical_entry(snapshot: dict, params: dict, open_positions: list) -> tuple:
    """Should this arm buy an opening debit vertical? Returns (enter, reason, plan | None).

    Mirror of `evaluate_credit_spread_entry`, buying instead of selling: the debit vertical bought
    here is completed later by SELLING the credit spread at the same centre
    (`evaluate_debit_completion`) -- literally the same two trades `legged` makes, in the opposite
    order.
    """
    if snapshot.get("dte", 0) != 0:
        return False, "no_0dte_expiration", None

    if before_open_gate(params, snapshot.get("now_min")):
        return False, "before_open_gate", None

    ok_window, window = in_entry_window(snapshot.get("now_min"), params.get("entry_windows", []))
    if not ok_window:
        return False, "outside_entry_window", None

    if len(open_positions) >= params.get("max_positions", 4):
        return False, "max_positions_reached", None

    if _window_cap_reached(params, open_positions, window):
        return False, "max_positions_this_window_reached", None

    center, center_reason = select_center(snapshot, params)
    if center is None:
        return False, center_reason, None

    if any(abs(p["center"] - center) < 1e-6 for p in open_positions):
        return False, "center_already_occupied", None

    width = params.get("wing_width", 5)
    side = choose_debit_side(snapshot, center)
    long_strike = center - width if side == CALL else center + width
    if not _have(snapshot, side, [center, long_strike]):
        return False, "missing_leg_quotes", None

    slip = params.get("slippage_frac", fly.DEFAULT_SLIPPAGE_FRAC)
    debit = fly.vertical_debit(quote(snapshot, side, long_strike), quote(snapshot, side, center), slip)

    if debit <= 0:
        # A non-positive modeled debit means a stale or crossed quote -- a debit vertical's value
        # is bounded below by zero, so nobody sells one for a credit.
        return False, "implausible_debit_quote", None

    min_debit = params.get("min_debit_pct_of_width", 0.20) * width
    if debit < min_debit:
        # Too cheap to be plausible: the far-OTM side of a debit spread this thin has little room
        # left to richen before the completing credit spread's own ceiling caps it.
        return False, "debit_below_floor_completion_implausible", None

    max_debit = params.get("max_debit_pct_of_width", 0.60) * width
    if debit > max_debit:
        # Mirror of legged's intrinsic-heavy ceiling: a debit approaching the width means the long
        # leg is deep ITM already, leaving little room for the completing sale to out-earn it.
        return False, "debit_above_ceiling_mostly_intrinsic", None

    symbol = snapshot["symbol"]
    qty = params.get("quantity", 1)
    open_fee = fly.vertical_open_fee(symbol, qty)
    fee_buffer = params.get("fee_buffer", 0.10)
    # The completing credit can never exceed `width` (a vertical's value is capped there), so a
    # debit that already leaves no room for buffer + both fee stacks, expressed in price points,
    # can never be out-earned -- refuse before ever taking the position on, the same feasibility
    # check `credit_cannot_clear_fees` is for legged's entry.
    completion_fee_est = fly.vertical_open_fee(symbol, qty)
    fees_in_points = (open_fee + completion_fee_est) / (fly.CONTRACT_MULTIPLIER * qty)
    if debit + fee_buffer + fees_in_points >= width:
        return False, "debit_cannot_be_out_earned", None

    return (
        True,
        "ok",
        {
            "side": side,
            "center": center,
            "center_reason": center_reason,
            "wing_width": width,
            "debit": round(debit, 4),
            "quantity": qty,
            "open_fee": open_fee,
            "completing_direction": fly.debit_first_completing_direction(side),
            "entry_window": window,
        },
    )


# --------------------------------------------------------------------------- debit-first entry (step 2)
def evaluate_debit_completion(snapshot: dict, position: dict, params: dict) -> tuple:
    """Should this open long vertical (debit_first's opening trade) be completed into a butterfly
    now, by SELLING the credit spread at the same centre? Returns (complete, reason, plan).

    Mirror of `evaluate_completion` with the trade direction reversed: the gate is
    `C > D + fee_buffer`, where D is the debit already paid.
    """
    if position.get("kind") != "long_vertical":
        return False, "not_a_debit_vertical", None

    side, center, width = position["side"], position["center"], position["wing_width"]
    # The completing credit spread is legged's own entry geometry -- short the centre, long the
    # wing on the far side, same formula `evaluate_credit_spread_entry` uses for its long_strike.
    wing_strike = center - width if side == PUT else center + width
    if not _have(snapshot, side, [center, wing_strike]):
        return False, "missing_leg_quotes", None

    slip = params.get("slippage_frac", fly.DEFAULT_SLIPPAGE_FRAC)
    credit = fly.vertical_credit(quote(snapshot, side, center), quote(snapshot, side, wing_strike), slip)

    symbol = snapshot["symbol"]
    qty = position.get("quantity", 1)
    completion_fee = fly.vertical_open_fee(symbol, qty)
    buffer_pts = params.get("fee_buffer", 0.10)
    debit_paid = -position["net"]  # net is negative (a debit) for an open long_vertical

    net = credit - debit_paid
    completed_fees = position.get("fees", 0.0) + completion_fee
    # Reuse fly.position_floor exactly as evaluate_completion does -- see its own comment on why
    # a completion's floor check must go through the shared function rather than a duplicate.
    floor = fly.position_floor(
        {
            "kind": "fly",
            "side": side,
            "center": center,
            "wing_width": width,
            "net": net,
            "quantity": qty,
            "fees": completed_fees,
        }
    )
    plan = {
        "credit": round(credit, 4),
        "net": round(net, 4),
        "completion_fee": completion_fee,
        "floor": round(floor, 2),
        "wing_strike": wing_strike,
        "gate_credit": round(debit_paid + buffer_pts, 4),  # the credit this would have had to beat
    }

    if credit <= debit_paid + buffer_pts:
        return False, "completing_credit_too_low", plan
    if floor < params.get("min_floor_dollars", 0.0):
        return False, "floor_below_minimum_after_fees", plan

    return True, "ok", plan


# --------------------------------------------------------------------------- iron completion
def evaluate_iron_completion(snapshot: dict, position: dict, params: dict) -> tuple:
    """Should this open credit spread be completed into an IRON butterfly now, by selling the
    OPPOSITE-type credit spread at the same centre? Returns (complete, reason, plan).

    Put held -> sell the call spread; call held -> sell the put spread -- the final geometry
    (long (center-w) put, short center put, short center call, long (center+w) call) is the same
    regardless of which side was legged first. Payoff-equivalent to a same-type fly shifted down
    by `wing_width`, so risk-free iff the two credits summed clear `wing_width` plus fees -- NOT
    assumed; the floor gate is what actually enforces it (`fly.iron_fly_payoff`,
    `fly.position_floor`'s iron_fly branch).
    """
    if position.get("kind") != "short_vertical":
        return False, "not_a_credit_spread", None

    side, center, width = position["side"], position["center"], position["wing_width"]
    opposite_side = CALL if side == PUT else PUT
    opposite_wing = center - width if opposite_side == PUT else center + width
    if not _have(snapshot, opposite_side, [center, opposite_wing]):
        return False, "missing_leg_quotes", None

    slip = params.get("slippage_frac", fly.DEFAULT_SLIPPAGE_FRAC)
    credit2 = fly.vertical_credit(
        quote(snapshot, opposite_side, center), quote(snapshot, opposite_side, opposite_wing), slip
    )

    symbol = snapshot["symbol"]
    qty = position.get("quantity", 1)
    completion_fee = fly.vertical_open_fee(symbol, qty)
    buffer_pts = params.get("fee_buffer", 0.10)
    credit1 = position["net"]

    net = credit1 + credit2
    completed_fees = position.get("fees", 0.0) + completion_fee
    floor = fly.position_floor(
        {
            "kind": "iron_fly",
            "center": center,
            "wing_width": width,
            "net": net,
            "quantity": qty,
            "fees": completed_fees,
        }
    )
    # The second credit this would have had to beat: with the first, enough to clear the width
    # (the iron fly's worst-case tent) plus the fee buffer.
    gate_credit = round(width + buffer_pts - credit1, 4)
    plan = {
        "credit": round(credit2, 4),
        "net": round(net, 4),
        "completion_fee": completion_fee,
        "floor": round(floor, 2),
        "opposite_side": opposite_side,
        "opposite_wing": opposite_wing,
        "gate_credit": gate_credit,
    }

    if credit2 <= gate_credit:
        return False, "iron_credit_too_low", plan
    if floor < params.get("min_floor_dollars", 0.0):
        return False, "floor_below_minimum_after_fees", plan

    return True, "ok", plan


# --------------------------------------------------------------------------- bwb entry (step 1)
def _bwb_lower_upper(side: str, near_wing: float, far_wing: float) -> tuple[float, float]:
    """Which of the two wings is numerically lower, by strike -- `fly.fly_debit`'s formula is
    symmetric in lower/upper (the mid terms just add), but the quotes must be looked up at the
    right strikes regardless."""
    return (far_wing, near_wing) if side == PUT else (near_wing, far_wing)


def evaluate_bwb_entry(snapshot: dict, params: dict, open_positions: list) -> tuple:
    """Should this arm enter a broken-wing butterfly for a net credit? Returns (enter, reason, plan).

    Side via `choose_side` (same heuristic legged uses). `far_width` (> wing_width) is the wide,
    risk-carrying wing; the credit collected is rent for that tail, measured against it
    (`min_bwb_credit_pct_of_tail`/`max_bwb_credit_pct_of_tail`), not against wing_width the way
    legged's credit gates are -- there is no width-bounded ceiling here since the structure's own
    worst case is already `wing_width - far_width`, not `-wing_width`.
    """
    if snapshot.get("dte", 0) != 0:
        return False, "no_0dte_expiration", None

    if before_open_gate(params, snapshot.get("now_min")):
        return False, "before_open_gate", None

    ok_window, window = in_entry_window(snapshot.get("now_min"), params.get("entry_windows", []))
    if not ok_window:
        return False, "outside_entry_window", None

    if len(open_positions) >= params.get("max_positions", 4):
        return False, "max_positions_reached", None

    if _window_cap_reached(params, open_positions, window):
        return False, "max_positions_this_window_reached", None

    center, center_reason = select_center(snapshot, params)
    if center is None:
        return False, center_reason, None

    if any(abs(p["center"] - center) < 1e-6 for p in open_positions):
        return False, "center_already_occupied", None

    width = params.get("wing_width", 5)
    # A ratio, not an absolute point value: wing_width itself is manually rescaled per symbol and
    # per width-sweep arm (control=1, width-2..width-5), and a fixed absolute far_width would need
    # separately rescaling every time either of those changes. A ratio (the common real-world rule
    # of thumb is roughly 1:2, near:far) scales automatically with whatever wing_width the arm is
    # already using.
    far_width = width * params.get("bwb_far_width_ratio", 2.0)
    if far_width <= width:
        return False, "far_width_not_wider_than_wing", None

    side = choose_side(snapshot, center)
    near_wing, _, far_wing = fly.bwb_strikes(side, center, width, far_width)
    if not _have(snapshot, side, [near_wing, center, far_wing]):
        return False, "missing_leg_quotes", None

    slip = params.get("slippage_frac", fly.DEFAULT_SLIPPAGE_FRAC)
    lower_wing, upper_wing = _bwb_lower_upper(side, near_wing, far_wing)
    # fly_debit prices the +1 lower / -2 centre / +1 upper combo as a debit (positive = cost);
    # a broken-wing entered for a net credit comes out NEGATIVE there, so credit is its negation.
    raw = fly.fly_debit(
        quote(snapshot, side, lower_wing), quote(snapshot, side, center), quote(snapshot, side, upper_wing), slip
    )
    credit = -raw

    tail = far_width - width
    min_credit = params.get("min_bwb_credit_pct_of_tail", 0.15) * tail
    if credit <= min_credit:
        return False, "bwb_credit_below_floor", None

    max_credit = params.get("max_bwb_credit_pct_of_tail", 0.6) * tail
    if credit > max_credit:
        return False, "bwb_credit_above_ceiling_mostly_intrinsic", None

    symbol = snapshot["symbol"]
    qty = params.get("quantity", 1)
    open_fee = fly.fly_open_fee(symbol, qty)
    # The tail risk this credit is being paid to carry, in dollars, net of the credit itself --
    # capped so no single structure can carry more downside than the operator has decided to
    # accept, independent of how the price gates above happen to price it.
    tail_dollars = (tail - credit) * fly.CONTRACT_MULTIPLIER * qty + open_fee
    if tail_dollars > params.get("max_bwb_tail_dollars", 150.0):
        return False, "bwb_tail_risk_above_max", None

    # The entry fee plus an anticipated roll fee (2-leg, same shape as legged's own completing
    # fee) -- a credit that cannot clear even that combined stack could never be justified
    # regardless of how the roll eventually prices.
    anticipated_roll_fee = fly.vertical_open_fee(symbol, qty)
    if credit * fly.CONTRACT_MULTIPLIER * qty <= open_fee + anticipated_roll_fee:
        return False, "credit_cannot_clear_fees", None

    return (
        True,
        "ok",
        {
            "side": side,
            "center": center,
            "center_reason": center_reason,
            "wing_width": width,
            "far_width": far_width,
            "credit": round(credit, 4),
            "quantity": qty,
            "open_fee": open_fee,
            "entry_window": window,
        },
    )


# --------------------------------------------------------------------------- bwb roll (step 2)
def evaluate_roll(snapshot: dict, position: dict, params: dict) -> tuple:
    """Should this open broken-wing butterfly be rolled now -- buying the near strike, selling the
    held far strike -- converting it into a symmetric fly at (credit - roll_debit)? Returns
    (roll, reason, plan).

    The roll is a 2-leg debit vertical of width (far_width - wing_width): buy the strike the
    symmetric fly needs, sell the wide wing currently held. Gated the same shape as every other
    completion in this module: price gate first (`roll_debit < credit - fee_buffer`), then the
    resulting fly's actual floor (`fly.position_floor`), never assumed from the price gate alone.
    """
    if position.get("kind") != "bwb":
        return False, "not_a_bwb", None

    side, center, width = position["side"], position["center"], position["wing_width"]
    far_width = position["far_width"]
    near_wing, _, far_wing = fly.bwb_strikes(side, center, width, far_width)
    if not _have(snapshot, side, [near_wing, far_wing]):
        return False, "missing_leg_quotes", None

    slip = params.get("slippage_frac", fly.DEFAULT_SLIPPAGE_FRAC)
    # Buy the near wing (what the symmetric fly needs), sell the far wing (currently held) --
    # a debit vertical, long near / short far.
    roll_debit = fly.vertical_debit(quote(snapshot, side, near_wing), quote(snapshot, side, far_wing), slip)

    symbol = snapshot["symbol"]
    qty = position.get("quantity", 1)
    roll_fee = fly.vertical_open_fee(symbol, qty)
    buffer_pts = params.get("fee_buffer", 0.10)
    credit = position["net"]

    net = credit - roll_debit
    rolled_fees = position.get("fees", 0.0) + roll_fee
    floor = fly.position_floor(
        {
            "kind": "fly",
            "side": side,
            "center": center,
            "wing_width": width,
            "net": net,
            "quantity": qty,
            "fees": rolled_fees,
        }
    )
    plan = {
        "roll_debit": round(roll_debit, 4),
        "net": round(net, 4),
        "roll_fee": roll_fee,
        "floor": round(floor, 2),
        "near_wing": near_wing,
        "gate_debit": round(credit - buffer_pts, 4),  # the roll debit this would have had to beat
    }

    if roll_debit >= credit - buffer_pts:
        return False, "roll_debit_too_high", plan
    if floor < params.get("min_floor_dollars", 0.0):
        return False, "floor_below_minimum_after_fees", plan

    return True, "ok", plan


# --------------------------------------------------------------------------- outright entry
def evaluate_outright_entry(
    snapshot: dict, params: dict, open_positions: list, realized_cash: float
) -> tuple:
    """Should the book buy a cheap fly outright, funded by premium it has already realized?

    `realized_cash` is the book's credit-minus-debits-minus-fees so far. Requiring the debit to fit
    inside it is what keeps this mode honest: the book never spends money it hasn't taken in, so its
    floor is bounded by construction. That floor is still only BOOK-level and only holds inside the
    funding spreads' wings — `fly.book_floor` reports the band, and callers must not round it up to
    "risk-free".
    """
    if snapshot.get("dte", 0) != 0:
        return False, "no_0dte_expiration", None

    # Checked BEFORE the arm's own windows so an arm cannot configure its way past the blackout.
    if before_open_gate(params, snapshot.get("now_min")):
        return False, "before_open_gate", None

    ok_window, window = in_entry_window(snapshot.get("now_min"), params.get("entry_windows", []))
    if not ok_window:
        return False, "outside_entry_window", None

    if len(open_positions) >= params.get("max_positions", 4):
        return False, "max_positions_reached", None

    if _window_cap_reached(params, open_positions, window):
        return False, "max_positions_this_window_reached", None

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
    debit = fly.fly_debit(
        quote(snapshot, side, lower), quote(snapshot, side, center), quote(snapshot, side, upper), slip
    )
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

    return (
        True,
        "ok",
        {
            "side": side,
            "center": center,
            "center_reason": center_reason,
            "wing_width": width,
            "debit": round(debit, 4),
            "quantity": qty,
            "open_fee": open_fee,
            "cost": round(cost, 2),
            "entry_window": window,
        },
    )


# --------------------------------------------------------------------------- settlement
def _expiry_payoff(position: dict, settlement_price: float) -> float:
    """Per-contract expiry value by kind — explicit dispatch, no fallthrough. A silent else here
    once priced whatever future kind arrived as a short vertical, which is a SIGN-FLIPPED payoff
    for anything whose worst case sits above zero (e.g. a debit-first long vertical) — the exact
    hazard this function exists to close off before a new kind is ever settled for real."""
    kind = position["kind"]
    if kind == "fly":
        return fly.fly_payoff(position["center"], position["wing_width"], settlement_price)
    if kind == "short_vertical":
        return fly.short_vertical_payoff(
            position["side"], position["center"], position["wing_width"], settlement_price
        )
    if kind == "long_vertical":
        return fly.debit_vertical_payoff(
            position["side"], position["center"], position["wing_width"], settlement_price
        )
    if kind == "iron_fly":
        return fly.iron_fly_payoff(position["center"], position["wing_width"], settlement_price)
    if kind == "bwb":
        return fly.bwb_payoff(
            position["side"],
            position["center"],
            position["wing_width"],
            position["far_width"],
            settlement_price,
        )
    raise ValueError(f"_expiry_payoff: unknown position kind {kind!r}")


def settle(positions: list[dict], settlement_price: float) -> list[dict]:
    """Cash-settle every open position at expiry. SPX/XSP are European cash-settled, so there is no
    physical assignment to model — but tastytrade still charges $5/contract the next business day
    for every leg that finishes ITM and is exercised into cash (see `fly.itm_legs_at_settlement`;
    OTM legs expire worthless for free). That charge is folded into the position's `fees` here, once,
    at the moment of settlement, so `pnl` and the persisted `fees` total both include it consistently.

    Deliberately there is no stop loss and no wing adjustment: once a structure exists it is held to
    settlement. v1 is measuring the base rate of this strategy, and an adjustment rule tuned before
    a single completion rate has been observed would be fitting noise.
    """
    out = []
    for p in positions:
        itm_legs = fly.itm_legs_at_settlement(p, settlement_price)
        assignment = fly.expire_fee(itm_legs)
        settled_fees = round(p.get("fees", 0.0) + assignment, 2)
        # status="settled" tells position_pnl the assignment fee is ALREADY folded into
        # settled_fees above -- without it, position_pnl would (correctly, for a still-open
        # position) compute its own fresh assignment fee from settlement_price and double-charge it.
        pnl = fly.position_pnl({**p, "fees": settled_fees, "status": "settled"}, settlement_price)
        out.append(
            {
                **p,
                "settlement_price": settlement_price,
                "expiry_payoff": round(_expiry_payoff(p, settlement_price), 4),
                "fees": settled_fees,
                "itm_legs": itm_legs,
                "assignment_fee": assignment,
                "pnl": round(pnl, 2),
                "pinned": p["kind"] in ("fly", "iron_fly")
                and abs(settlement_price - p["center"]) < p["wing_width"],
                "status": "settled",
            }
        )
    return out


def session_stats(positions: list[dict]) -> dict:
    """The three numbers the whole thesis turns on, per session.

    completion_rate  how often a credit spread actually became a fly. If this is near zero the
                     strategy is just short verticals wearing a costume.
    risk_free_rate   share of flies whose floor survived fees.
    pin_rate         share of flies that finished inside their wings (settled positions only).
    """
    flies = [p for p in positions if p["kind"] == "fly"]
    iron_flies = [p for p in positions if p["kind"] == "iron_fly"]
    bwbs = [p for p in positions if p["kind"] == "bwb"]
    legged = [p for p in positions if p.get("entry_mode") == "legged"]
    # A completion is a completion regardless of which completing trade closed it out -- an iron
    # completion counts toward legged's completion_rate exactly like a debit completion does.
    legged_completed = [p for p in legged if p["kind"] in ("fly", "iron_fly")]
    settled_flies = [p for p in flies + iron_flies if p.get("status") == "settled"]
    debit_first = [p for p in positions if p.get("entry_mode") == "debit_first"]
    debit_first_flies = [p for p in debit_first if p["kind"] == "fly"]
    bwb_roll_entries = [p for p in positions if p.get("entry_mode") == "bwb_roll"]
    bwb_rolled = [p for p in bwb_roll_entries if p["kind"] == "fly"]

    def _rate(n, d):
        return round(n / d, 4) if d else None

    return {
        "positions": len(positions),
        "flies": len(flies),
        "iron_completions": len(iron_flies),
        # Named for what it counts: structures that never became flies. This counts settled ones too,
        # because after the bell "still a vertical" is the outcome, not a transient state.
        "uncompleted_verticals": len([p for p in positions if p["kind"] == "short_vertical"]),
        "uncompleted_long_verticals": len([p for p in positions if p["kind"] == "long_vertical"]),
        # Every bwb still in this kind never got rolled to a symmetric fly -- its real, negative-
        # capable floor is the finding, same spirit as uncompleted_verticals.
        "unrolled_bwbs": len(bwbs),
        "completion_rate": _rate(len(legged_completed), len(legged)),
        "debit_first_completion_rate": _rate(len(debit_first_flies), len(debit_first)),
        "bwb_roll_rate": _rate(len(bwb_rolled), len(bwb_roll_entries)),
        # Includes iron flies and unrolled bwbs -- both carry a floor that can legitimately be
        # negative (unlike a same-type fly's), so they belong in this rate exactly as much as a
        # fly does; excluding either would silently hide the case this rate exists to catch.
        "risk_free_rate": _rate(
            len([p for p in flies + iron_flies + bwbs if fly.is_risk_free(p)]),
            len(flies) + len(iron_flies) + len(bwbs),
        ),
        "pin_rate": _rate(len([p for p in settled_flies if p.get("pinned")]), len(settled_flies)),
    }
