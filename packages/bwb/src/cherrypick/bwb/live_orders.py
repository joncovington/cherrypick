"""Pure order builders and sizing rules for the bwb LIVE path.

Every function here is a pure function over a plan `engine.py` already produced -- no I/O, no
clock, no broker -- so the live loop's decisions can be pinned by tests the same way the paper
loop's are. Three things live here and nowhere else:

  entry_spec / addon_spec   the broker order for a plan: the paper legs collapsed into the shape
                            the seam submits (the body's two rows become ONE sell leg at double
                            quantity), a limit at mid minus a concession floored to the tick, and
                            a refusal below the live floor.
  live_floor / next_limit   the cost-derived floor a live limit may never go under, and the
                            bounded walk-down toward it when a resting order is not filling.
  worst_case_loss_dollars / margin_cap_exceeded
                            the local margin ceiling, over expiry payoffs computed from the legs
                            themselves, with an unfired arm's future add-on RESERVED so a later
                            fire can neither breach the cap nor be refused by it.

None of this changes what is entered: the structure is `engine.plan_entry`'s, so a live row stays
comparable with its paper twin. It changes only whether and at what price the order is submitted.
"""

from __future__ import annotations

from cherrypick.core.structures import TICK, tick_ceil, tick_floor  # one rounding rule, suite-wide

from cherrypick.bwb import engine

INSTRUMENT_TYPE = "Equity Option"  # what tastytrade calls an SPXW option leg (the smoke-tested shape)
_ENTRY_ROLES = ("near_long", "body_short_1", "body_short_2", "far_long")
_ADDON_ROLES = ("addon_short", "addon_long")


# --------------------------------------------------------------------------- specs
def _leg(occ_symbol: str, action: str, quantity: int) -> dict:
    return {
        "instrument_type": INSTRUMENT_TYPE,
        "symbol": occ_symbol,
        "action": action,
        "quantity": int(quantity),
    }


def _by_role(legs: list[dict], roles: tuple[str, ...]) -> dict[str, dict]:
    found = {leg["leg_role"]: leg for leg in legs if leg.get("leg_role") in roles}
    missing = [r for r in roles if r not in found]
    if missing:
        raise ValueError(f"plan is missing legs {missing}")
    return found


def entry_spec(plan: dict, quantity: int, concession: float, *, floor: float | None = None) -> dict:
    """The base BWB as one broker order: buy the near wing, SELL THE BODY AT 2x QUANTITY, buy the
    far wing, as a Day limit at `tick_floor(credit - concession)` for a credit.

    The paper ledger carries the body as two leg rows so per-leg marks and settlement stay
    uniform; the broker wants one leg at quantity 2. Refuses (ValueError) a price at or below
    zero, and below `floor` when one is given -- a limit the fee stack would turn into a loss is
    not an order this pilot places."""
    legs = _by_role(plan["legs"], _ENTRY_ROLES)
    if legs["body_short_1"]["occ_symbol"] != legs["body_short_2"]["occ_symbol"]:
        raise ValueError("body legs are not the same contract")
    price = tick_floor(float(plan["credit"]) - float(concession))
    if price <= 0:
        raise ValueError(f"entry credit {plan['credit']} less concession floors to {price}: not a credit")
    if floor is not None and price < floor:
        raise ValueError(f"credit_below_live_floor: limit {price:.2f} is under the live floor {floor:.2f}")
    return {
        "time_in_force": "Day",
        "order_type": "Limit",
        "legs": [
            _leg(legs["near_long"]["occ_symbol"], "Buy to Open", quantity),
            _leg(legs["body_short_1"]["occ_symbol"], "Sell to Open", 2 * quantity),
            _leg(legs["far_long"]["occ_symbol"], "Buy to Open", quantity),
        ],
        "price": price,
        "price_effect": "credit",
    }


def addon_spec(plan: dict, quantity: int, concession: float, *, floor: float | None = None) -> dict:
    """The 1-3-2 add-on as one broker order: the put credit spread bracketing the far wing."""
    legs = _by_role(plan["legs"], _ADDON_ROLES)
    price = tick_floor(float(plan["credit"]) - float(concession))
    if price <= 0:
        raise ValueError(f"add-on credit {plan['credit']} less concession floors to {price}: not a credit")
    if floor is not None and price < floor:
        raise ValueError(f"credit_below_live_floor: limit {price:.2f} is under the live floor {floor:.2f}")
    return {
        "time_in_force": "Day",
        "order_type": "Limit",
        "legs": [
            _leg(legs["addon_short"]["occ_symbol"], "Sell to Open", quantity),
            _leg(legs["addon_long"]["occ_symbol"], "Buy to Open", quantity),
        ],
        "price": price,
        "price_effect": "credit",
    }


def with_price(spec: dict, price: float) -> dict:
    """The same order at a new limit (cancel/replace)."""
    return {**spec, "price": float(price)}


# --------------------------------------------------------------------------- floors and the walk-down
def live_floor(fees_dollars: float, min_net_dollars: float, quantity: int) -> float:
    """The lowest credit a live limit may ask: fees plus the declared minimum net, per contract,
    ceiled to the tick. Modeled slippage is deliberately NOT in here -- a limit fill IS the credit,
    so the concession the limit already gave up is the slippage accepted, and adding the model
    would count it twice. `fees_dollars` is the broker's dry-run estimate when one exists, else
    the schedule (`engine.entry_cost` / `engine.addon_entry_cost`)."""
    per_contract = (float(fees_dollars) + float(min_net_dollars)) / 100.0 / max(int(quantity), 1)
    return tick_ceil(per_contract)


def reprice_needed(working: float, fresh: float) -> bool:
    """A cancel/replace is worth a broker round-trip only when the limit would move a full tick."""
    cents = abs(int(round(float(working) * 100)) - int(round(float(fresh) * 100)))
    return cents >= int(round(TICK * 100))


def next_limit(
    working: float, fresh_mid: float, concession: float, floor: float, *, steps_taken: int
) -> float | None:
    """The bounded walk-down. The target is the fresh market's limit (mid minus concession) less
    one tick per step already taken, never below `floor`. Returns the new limit when it differs
    from `working` by a tick or more, else None (rest where it is). A rising mid lifts the target
    back up -- only the step count is monotonic -- and a step that would cross the floor lands ON
    the floor, after which the order rests there until cutoff."""
    target = tick_floor(float(fresh_mid) - float(concession)) - TICK * int(steps_taken)
    target = max(tick_ceil(float(floor)), round(target, 2))
    if not reprice_needed(working, target):
        return None
    return round(target, 2)


# --------------------------------------------------------------------------- margin
def _signed_quantity(leg: dict) -> int:
    qty = int(leg.get("quantity") or 1)
    return -qty if leg.get("action") == "Sell to Open" else qty


def worst_case_loss_dollars(legs: list[dict], net_credit: float, quantity: int) -> float:
    """The largest expiry loss, in dollars, of a set of option legs held to settlement, net of the
    credit received: the payoff is evaluated at every strike and at zero (piecewise-linear payoffs
    can only kink at strikes), and the worst is what the account must be able to lose.

    `legs` are ledger/plan leg dicts (`strike`, `option_type`, `action`, optional `quantity` as a
    structure ratio); `quantity` is the position quantity. For a bare BWB this equals
    `entry_max_loss * 100 * quantity` from `engine.bwb_metrics`, which a test pins."""
    strikes = sorted({float(leg["strike"]) for leg in legs})
    worst = 0.0
    for spot in [0.0, *strikes, strikes[-1] + 1.0 if strikes else 1.0]:
        payoff = 0.0
        for leg in legs:
            intrinsic = engine.settle_intrinsic(float(leg["strike"]), spot, leg.get("option_type") or "put")
            payoff += _signed_quantity(leg) * intrinsic
        worst = min(worst, payoff + float(net_credit))
    return round(-worst * 100.0 * max(int(quantity), 1), 2)


def addon_reserve_dollars(position: dict, params: dict) -> float:
    """What an unfired arm's future add-on could lose at worst: a put credit spread one increment
    wide on each side of the far wing, so its width is two increments. Reserved against the cap
    for every open, unfired position while the arm is not `control`, so the fire that comes later
    can never breach the cap or be refused by it. The credit it will collect is unknown and is
    counted as zero (the conservative side)."""
    increment = float(params.get("strike_increment") or 5.0)
    return round(2.0 * increment * 100.0 * int(position.get("quantity") or 1), 2)


def position_worst_case_dollars(
    position: dict, legs: list[dict], *, params: dict, reserve_addon: bool
) -> float:
    """One open position's worst case from its ledger legs plus, when asked, its add-on reserve."""
    credit = float(position.get("entry_credit") or 0.0) + float(position.get("addon_credit") or 0.0)
    total = worst_case_loss_dollars(legs, credit, int(position.get("quantity") or 1))
    if reserve_addon and not position.get("addon_fired_at"):
        total += addon_reserve_dollars(position, params)
    return round(total, 2)


def margin_cap_exceeded(
    cap_total: float | None,
    cap_per_expiration: float | None,
    open_positions: list[tuple[dict, list[dict]]],
    proposed_legs: list[dict],
    proposed_credit: float,
    proposed_expiration: str,
    quantity: int,
    *,
    params: dict,
    reserve_addons: bool,
) -> tuple[bool, dict]:
    """Would adding the proposed structure push the open book past either cap? Both caps are
    worst-case dollars at expiry (never fees or margin as the broker computes it). `None` for a cap
    means that cap is off. Returns `(exceeded, totals)` so the refusal can say the numbers."""
    proposed = worst_case_loss_dollars(proposed_legs, proposed_credit, quantity)
    if reserve_addons:
        proposed += addon_reserve_dollars({"quantity": quantity}, params)
    total = proposed
    per_exp = proposed
    for position, legs in open_positions:
        loss = position_worst_case_dollars(position, legs, params=params, reserve_addon=reserve_addons)
        total += loss
        if position.get("expiration") == proposed_expiration:
            per_exp += loss
    totals = {"proposed": round(proposed, 2), "total": round(total, 2), "expiration": round(per_exp, 2)}
    if cap_total is not None and total > float(cap_total):
        return True, {**totals, "cap": "total", "limit": float(cap_total)}
    if cap_per_expiration is not None and per_exp > float(cap_per_expiration):
        return True, {**totals, "cap": "expiration", "limit": float(cap_per_expiration)}
    return False, totals
