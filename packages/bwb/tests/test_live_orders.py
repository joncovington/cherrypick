"""The live order builders and sizing rules -- pure, so every rule is pinned here."""

from __future__ import annotations

import pytest
from conftest import occ

from cherrypick.bwb import engine, live_orders

EXP = "2026-09-25"


def _leg(role, strike, action, right="P"):
    return {
        "leg_role": role,
        "occ_symbol": occ("SPXW", EXP, strike, right),
        "streamer_symbol": f".SPXW{strike:g}",
        "expiration": EXP,
        "strike": strike,
        "option_type": "put" if right == "P" else "call",
        "action": action,
        "bid": 1.0,
        "ask": 1.2,
        "mid": 1.1,
    }


def _entry_plan(credit=0.9, body=7600.0):
    return {
        "symbol": "SPX",
        "expiration": EXP,
        "credit": credit,
        "body_strike": body,
        "near_strike": body + 5,
        "far_strike": body - 10,
        "legs": [
            _leg("near_long", body + 5, "Buy to Open"),
            _leg("body_short_1", body, "Sell to Open"),
            _leg("body_short_2", body, "Sell to Open"),
            _leg("far_long", body - 10, "Buy to Open"),
        ],
    }


def _addon_plan(credit=0.5, far=7590.0):
    return {
        "credit": credit,
        "short_strike": far + 5,
        "long_strike": far - 5,
        "legs": [
            _leg("addon_short", far + 5, "Sell to Open"),
            _leg("addon_long", far - 5, "Buy to Open"),
        ],
    }


# --------------------------------------------------------------------------- specs
def test_entry_spec_collapses_the_body_into_one_sell_leg_at_double_quantity():
    spec = live_orders.entry_spec(_entry_plan(0.9), 2, 0.05)
    assert spec["time_in_force"] == "Day" and spec["order_type"] == "Limit"
    assert spec["price_effect"] == "credit" and spec["price"] == pytest.approx(0.85)
    assert [(leg["action"], leg["quantity"]) for leg in spec["legs"]] == [
        ("Buy to Open", 2),
        ("Sell to Open", 4),
        ("Buy to Open", 2),
    ]
    assert spec["legs"][1]["symbol"] == occ("SPXW", EXP, 7600.0)
    assert all(leg["instrument_type"] == "Equity Option" for leg in spec["legs"])
    assert "external_identifier" not in spec  # the caller stamps identity, not the builder


def test_specs_floor_to_the_tick_and_refuse_a_non_credit_or_a_sub_floor_limit():
    assert live_orders.entry_spec(_entry_plan(0.93), 1, 0.05)["price"] == pytest.approx(0.85)
    with pytest.raises(ValueError, match="not a credit"):
        live_orders.entry_spec(_entry_plan(0.04), 1, 0.05)
    with pytest.raises(ValueError, match="credit_below_live_floor"):
        live_orders.entry_spec(_entry_plan(0.28), 1, 0.05, floor=0.25)
    assert live_orders.entry_spec(_entry_plan(0.30), 1, 0.05, floor=0.25)["price"] == pytest.approx(0.25)
    spec = live_orders.addon_spec(_addon_plan(0.5), 1, 0.05)
    assert [(leg["action"], leg["quantity"]) for leg in spec["legs"]] == [
        ("Sell to Open", 1),
        ("Buy to Open", 1),
    ]
    assert spec["price"] == pytest.approx(0.45)
    with pytest.raises(ValueError, match="credit_below_live_floor"):
        live_orders.addon_spec(_addon_plan(0.2), 1, 0.05, floor=0.20)


def test_entry_spec_refuses_a_plan_whose_body_rows_disagree_or_whose_legs_are_missing():
    plan = _entry_plan()
    plan["legs"][2]["occ_symbol"] = occ("SPXW", EXP, 7605.0)
    with pytest.raises(ValueError, match="same contract"):
        live_orders.entry_spec(plan, 1, 0.05)
    with pytest.raises(ValueError, match="missing legs"):
        live_orders.entry_spec({"credit": 1.0, "legs": plan["legs"][:2]}, 1, 0.05)


# --------------------------------------------------------------------------- floors and walk-down
def test_live_floor_is_fees_plus_min_net_ceiled_to_the_tick_and_never_includes_slippage():
    # the schedule's SPX 4-leg/2-sell open fee is 6.89; the add-on's 2-leg/1-sell is 3.44
    entry_fee = engine.entry_cost("SPX", [], 1, {})["fee"]
    addon_fee = engine.addon_entry_cost("SPX", [], 1, {})["fee"]
    assert entry_fee == pytest.approx(6.89) and addon_fee == pytest.approx(3.44)
    assert live_orders.live_floor(entry_fee, 15.0, 1) == pytest.approx(0.25)
    assert live_orders.live_floor(addon_fee, 15.0, 1) == pytest.approx(0.20)
    assert live_orders.live_floor(entry_fee, 0.0, 1) == pytest.approx(0.10)  # fees only
    assert live_orders.live_floor(2 * entry_fee, 15.0, 2) == pytest.approx(0.15)  # per contract
    # a broker estimate above the schedule lifts the floor with it
    assert live_orders.live_floor(9.60, 15.0, 1) == pytest.approx(0.25)
    assert live_orders.live_floor(10.10, 15.0, 1) == pytest.approx(0.25)  # 25.1c is 25c: cents first
    assert live_orders.live_floor(10.60, 15.0, 1) == pytest.approx(0.30)


def test_next_limit_walks_down_one_tick_per_step_and_lands_on_the_floor():
    # fresh mid 0.90, concession 0.05, floor 0.75: 0.85 -> 0.80 -> 0.75 -> rest
    assert live_orders.next_limit(0.85, 0.90, 0.05, 0.75, steps_taken=1) == pytest.approx(0.80)
    assert live_orders.next_limit(0.80, 0.90, 0.05, 0.75, steps_taken=2) == pytest.approx(0.75)
    assert live_orders.next_limit(0.75, 0.90, 0.05, 0.75, steps_taken=3) is None  # on the floor: rest
    # a step that would cross the floor lands ON it (the floor itself ceils to the tick), and an
    # order already resting on it makes no call
    assert live_orders.next_limit(0.85, 0.90, 0.05, 0.78, steps_taken=2) == pytest.approx(0.80)
    assert live_orders.next_limit(0.80, 0.90, 0.05, 0.78, steps_taken=2) is None


def test_next_limit_follows_a_rising_mid_back_up_and_makes_no_call_for_a_sub_tick_move():
    # two steps taken, but the mid rallied from 0.90 to 1.10: the target lifts to 1.05 - 0.10
    assert live_orders.next_limit(0.75, 1.10, 0.05, 0.25, steps_taken=2) == pytest.approx(0.95)
    # the market drifted a cent: not worth a cancel/replace
    assert live_orders.next_limit(0.85, 0.91, 0.05, 0.25, steps_taken=0) is None
    assert live_orders.reprice_needed(0.85, 0.80) and not live_orders.reprice_needed(0.85, 0.84)


# --------------------------------------------------------------------------- margin
def test_worst_case_of_a_bare_bwb_equals_engine_max_loss():
    plan = _entry_plan(0.9)
    metrics = engine.bwb_metrics(
        body_mid=1.5, near_mid=1.9, far_mid=0.2, body_strike=7600.0, near_strike=7605.0, far_strike=7590.0
    )
    assert metrics["credit"] == pytest.approx(0.9)
    worst = live_orders.worst_case_loss_dollars(plan["legs"], 0.9, 1)
    assert worst == pytest.approx(metrics["max_loss"] * 100)
    assert live_orders.worst_case_loss_dollars(plan["legs"], 0.9, 3) == pytest.approx(
        metrics["max_loss"] * 300
    )


def test_worst_case_of_a_fired_132_is_bounded_and_the_reserve_covers_it():
    legs = _entry_plan(0.9)["legs"] + _addon_plan(0.5)["legs"]
    bare = live_orders.worst_case_loss_dollars(_entry_plan()["legs"], 0.9, 1)
    fired = live_orders.worst_case_loss_dollars(legs, 0.9 + 0.5, 1)
    # the add-on is a $10-wide credit spread below the far wing: the combined worst case grows
    # by at most that width less the add-on credit, and the reserve (width, credit counted as 0)
    # covers the growth
    assert (
        bare < fired <= bare + live_orders.addon_reserve_dollars({"quantity": 1}, {"strike_increment": 5.0})
    )
    assert live_orders.addon_reserve_dollars({"quantity": 2}, {"strike_increment": 5.0}) == 2000.0


def test_margin_caps_reserve_the_addon_only_for_unfired_positions_when_the_arm_can_fire():
    params = {"strike_increment": 5.0}
    plan = _entry_plan(0.9)
    open_pos = ({"entry_credit": 0.9, "quantity": 1, "expiration": EXP, "addon_fired_at": None}, plan["legs"])
    bare = live_orders.worst_case_loss_dollars(plan["legs"], 0.9, 1)  # (10 - 5 - 0.9) * 100 = 410
    kw = dict(
        proposed_legs=plan["legs"], proposed_credit=0.9, proposed_expiration=EXP, quantity=1, params=params
    )
    # control: no reserve -> 2 x 410 = 820 fits under 2000
    hit, totals = live_orders.margin_cap_exceeded(2000.0, None, [open_pos], reserve_addons=False, **kw)
    assert not hit and totals["total"] == pytest.approx(2 * bare)
    # an arm that can fire: each unfired position reserves 1000 -> 3820 > 2000
    hit, totals = live_orders.margin_cap_exceeded(2000.0, None, [open_pos], reserve_addons=True, **kw)
    assert hit and totals["cap"] == "total" and totals["total"] == pytest.approx(2 * bare + 2000)
    # a FIRED position carries its real add-on legs and reserves nothing more
    fired_pos = (
        {"entry_credit": 0.9, "addon_credit": 0.5, "quantity": 1, "expiration": EXP, "addon_fired_at": "t"},
        plan["legs"] + _addon_plan(0.5)["legs"],
    )
    hit, totals = live_orders.margin_cap_exceeded(5000.0, None, [fired_pos], reserve_addons=True, **kw)
    assert not hit
    fired_worst = live_orders.worst_case_loss_dollars(fired_pos[1], 1.4, 1)
    assert totals["total"] == pytest.approx(bare + 1000 + fired_worst)


def test_per_expiration_cap_trips_before_the_total_and_null_caps_are_off():
    params = {"strike_increment": 5.0}
    plan = _entry_plan(0.9)
    same = ({"entry_credit": 0.9, "quantity": 1, "expiration": EXP}, plan["legs"])
    other = ({"entry_credit": 0.9, "quantity": 1, "expiration": "2026-10-02"}, plan["legs"])
    kw = dict(
        proposed_legs=plan["legs"], proposed_credit=0.9, proposed_expiration=EXP, quantity=1, params=params
    )
    hit, totals = live_orders.margin_cap_exceeded(10_000.0, 800.0, [same, other], reserve_addons=False, **kw)
    assert hit and totals["cap"] == "expiration" and totals["expiration"] == pytest.approx(820)
    assert totals["total"] == pytest.approx(1230)
    hit, _ = live_orders.margin_cap_exceeded(None, None, [same, other], reserve_addons=False, **kw)
    assert not hit
