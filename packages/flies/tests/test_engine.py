"""Unit tests for the entry / completion decision engine."""

import pytest

import engine
import fly

BASE_CONFIG = {
    "defaults": {
        "wing_width": 5, "strike_increment": 5, "quantity": 1, "max_positions": 4,
        "entry_modes": ["legged", "outright"], "min_credit_pct_of_width": 0.20,
        "max_credit_pct_of_width": 0.60,
        "fee_buffer": 0.10, "min_floor_dollars": 0.0, "max_fly_debit": 0.50,
        "max_center_distance_pct": 0.01, "slippage_frac": 0.125, "entry_windows": [],
    },
    "arms": {"gex": {}, "time_window": {}, "control": {}},
}


def q(bid, ask):
    return {"bid": bid, "ask": ask}


def snapshot(**over):
    """A 0DTE SPX snapshot with spot at 6000 and a plausible put/call grid around it."""
    snap = {
        "symbol": "SPX", "date": "2026-07-20", "dte": 0, "underlying_price": 6000.0,
        "now_min": 12 * 60,
        "puts": {5990: q(1.0, 1.4), 5995: q(2.6, 3.0), 6000: q(5.0, 5.4), 6005: q(8.6, 9.0)},
        "calls": {5995: q(8.6, 9.0), 6000: q(5.0, 5.4), 6005: q(2.6, 3.0), 6010: q(1.0, 1.4)},
    }
    snap.update(over)
    return snap


def params(arm="control", **over):
    p = engine.merged_params(BASE_CONFIG, arm)
    p.update(over)
    return p


# --------------------------------------------------------------------------- centre selection
def test_control_and_time_window_arms_center_atm():
    for arm in ("control", "time_window"):
        center, reason = engine.select_center(snapshot(underlying_price=6002.0), params(arm))
        assert (center, reason) == (6000.0, "atm")


def test_gex_arm_centers_on_max_total_gamma():
    """Total gamma (call + put), not net GEX.

    Pinning comes from dealer gamma CONCENTRATION, which does not care which side the gamma sits on.
    The strike below with huge call and huge put gamma is the hardest-pinning one, and nets to
    roughly zero — the old net-GEX rule would have passed straight over it.
    """
    gex = {"ok": True, "per_strike": [
        {"strike": 5990, "call_gex": 1_000, "put_gex": 500, "net_gex": 500},
        {"strike": 6005, "call_gex": 90_000, "put_gex": 88_000, "net_gex": 2_000},
        {"strike": 6010, "call_gex": 9_000, "put_gex": 1_000, "net_gex": 8_000},
    ]}
    center, reason = engine.select_center(snapshot(gex=gex), params("gex"))
    assert (center, reason) == (6005.0, "max_total_gamma")


def test_gex_arm_finds_a_strike_where_net_gex_is_negative_everywhere():
    """The case measured on a real SPX chain: put open interest dominates every strike near spot, so
    net GEX is negative across the whole neighbourhood and the old rule had nothing to select."""
    gex = {"ok": True, "per_strike": [
        {"strike": 5995, "call_gex": 10_000, "put_gex": 200_000, "net_gex": -190_000},
        {"strike": 6005, "call_gex": 40_000, "put_gex": 800_000, "net_gex": -760_000},
    ]}
    center, reason = engine.select_center(snapshot(gex=gex), params("gex"))
    assert (center, reason) == (6005.0, "max_total_gamma")


def test_gex_arm_ignores_strikes_beyond_the_distance_cap():
    """A huge GEX pile 3% away is not a 0DTE pin candidate — the cap keeps the arm centred near spot."""
    gex = {"ok": True, "per_strike": [
        {"strike": 6005, "call_gex": 1_000, "put_gex": 0, "net_gex": 1_000},
        {"strike": 6200, "call_gex": 900_000, "put_gex": 0, "net_gex": 900_000},
    ]}
    center, _ = engine.select_center(snapshot(gex=gex), params("gex"))
    assert center == 6005.0


def test_gex_arm_degrades_to_atm_and_records_why():
    """A streamer that hasn't cached open interest yet should cost us a signal, not a whole session
    of samples — but the degrade has to be visible so those trades can be excluded later."""
    center, reason = engine.select_center(snapshot(gex={"ok": False}), params("gex"))
    assert center == 6000.0 and reason == "atm_gex_unavailable"

    gex = {"ok": True, "per_strike": [{"strike": 6200, "call_gex": 5_000, "put_gex": 5_000}]}
    center, reason = engine.select_center(snapshot(gex=gex), params("gex"))
    assert center == 6000.0 and reason == "atm_no_gamma_near_spot"


def test_side_choice_follows_spot_relative_to_center():
    """Sell the spread whose completing leg has room to cheapen if the current drift continues."""
    assert engine.choose_side(snapshot(underlying_price=5998.0), 6000) == "put"
    assert engine.choose_side(snapshot(underlying_price=6002.0), 6000) == "call"


# --------------------------------------------------------------------------- entry windows
def test_entry_window_gate():
    windows = [["09:45", "10:15"], ["13:00", "13:30"]]
    assert engine.in_entry_window(10 * 60, windows) == (True, "09:45-10:15")
    assert engine.in_entry_window(13 * 60 + 15, windows) == (True, "13:00-13:30")
    assert engine.in_entry_window(11 * 60, windows) == (False, None)
    assert engine.in_entry_window(None, windows) == (False, None)
    assert engine.in_entry_window(11 * 60, []) == (True, None)  # no windows = always open


def test_entry_is_tagged_with_its_window_for_later_ranking():
    """We have no intraday history to rank windows with, so v1 records the window and lets the
    ranking emerge from our own sessions instead of assuming one."""
    p = params("time_window", entry_windows=[["11:00", "11:30"]])
    enter, _, plan = engine.evaluate_credit_spread_entry(
        snapshot(now_min=11 * 60 + 10, underlying_price=5998.0), p, [])
    assert enter and plan["entry_window"] == "11:00-11:30"


# --------------------------------------------------------------------------- legged entry (step 1)
def test_credit_spread_entry_returns_a_complete_plan():
    enter, reason, plan = engine.evaluate_credit_spread_entry(
        snapshot(underlying_price=5998.0), params(), [])
    assert enter and reason == "ok"
    assert plan["side"] == "put" and plan["center"] == 6000.0
    # Completing a put fly centred at 6000 means buying the 6005/6000 put debit spread.
    assert plan["completing_strike"] == 6005.0
    assert plan["completing_direction"] == "up"


def test_entry_requires_0dte():
    enter, reason, _ = engine.evaluate_credit_spread_entry(snapshot(dte=1), params(), [])
    assert not enter and reason == "no_0dte_expiration"


def test_entry_respects_the_position_cap():
    open_positions = [{"center": 5000 + i, "kind": "fly"} for i in range(4)]
    enter, reason, _ = engine.evaluate_credit_spread_entry(snapshot(), params(), open_positions)
    assert not enter and reason == "max_positions_reached"


def test_entry_will_not_stack_two_structures_on_one_center():
    """Two flies on the same strike double the pin bet without adding a profit zone — the opposite of
    what a forest of separate zones is for."""
    enter, reason, _ = engine.evaluate_credit_spread_entry(
        snapshot(underlying_price=5998.0), params(), [{"center": 6000.0, "kind": "fly"}])
    assert not enter and reason == "center_already_occupied"


def test_entry_rejects_a_credit_below_the_floor():
    thin = snapshot(underlying_price=5998.0,
                    puts={5990: q(4.8, 5.2), 5995: q(4.9, 5.3), 6000: q(5.0, 5.4), 6005: q(8.6, 9.0)})
    enter, reason, _ = engine.evaluate_credit_spread_entry(thin, params(), [])
    assert not enter and reason == "credit_below_floor"


def test_entry_rejects_an_intrinsic_heavy_credit():
    """The fault that real SPX data exposed and synthetic quotes could not.

    A vertical cannot be worth more than its width, so a credit near that width means the short leg
    is deep in the money and the premium is almost entirely intrinsic. Selling one is a
    low-probability directional bet — profitable only on a large move toward the strike — which is
    the opposite of a pin bet.

    Modelled on the real case: spot 7457.69, a centre 67 points away at 7525, and a short
    7525/7520 put spread paying 3.85 on a 5-wide (77% of width) with 67 points of intrinsic.
    """
    deep_itm = snapshot(
        underlying_price=7457.69,
        gex={"ok": True, "per_strike": [{"strike": 7525, "call_gex": 900_000, "put_gex": 0}]},
        puts={7525: q(70.0, 70.6), 7520: q(65.4, 66.0)})
    # The OLD distance cap, so the gex arm reaches the wall exactly as it did against live quotes.
    p = params("gex", strike_increment=5, wing_width=5, max_center_distance_pct=0.01)
    assert engine.select_center(deep_itm, p)[0] == 7525, "fixture must reproduce the far centre"

    enter, reason, _ = engine.evaluate_credit_spread_entry(deep_itm, p, [])
    assert not enter and reason == "credit_above_ceiling_mostly_intrinsic"


def test_the_ceiling_does_not_block_a_normal_atm_entry():
    """The counterweight — a ceiling that blocked ordinary entries would be worse than no ceiling.
    A real ATM SPX 5-wide priced at 41% of width, comfortably under the 60% cap."""
    enter, reason, plan = engine.evaluate_credit_spread_entry(
        snapshot(underlying_price=5998.0), params(), [])
    assert enter, reason
    assert plan["credit"] / plan["wing_width"] < 0.60


def test_the_two_defenses_are_independent():
    """The distance cap and the credit ceiling must each stop this on their own.

    They guard the same fault from different sides, and a single defense would be one config edit
    away from silently reopening it: tightening the cap alone would leave nothing to catch an
    intrinsic-heavy spread if someone later loosened it for a legitimate reason.
    """
    deep_itm = snapshot(
        underlying_price=7457.69,
        gex={"ok": True, "per_strike": [{"strike": 7525, "call_gex": 900_000, "put_gex": 0}]},
        puts={7460: q(5.0, 5.4), 7455: q(3.0, 3.4),
              7525: q(70.0, 70.6), 7520: q(65.4, 66.0)})

    # 1. Distance cap alone (ceiling disabled): the arm never reaches the far strike.
    tight = params("gex", strike_increment=5, wing_width=5,
                   max_center_distance_pct=0.003, max_credit_pct_of_width=99.0)
    enter, _, plan = engine.evaluate_credit_spread_entry(deep_itm, tight, [])
    assert enter and plan["center"] == 7460

    # 2. Ceiling alone (cap loosened back to what shipped): it reaches the strike and is refused.
    loose = params("gex", strike_increment=5, wing_width=5, max_center_distance_pct=0.01)
    enter, reason, _ = engine.evaluate_credit_spread_entry(deep_itm, loose, [])
    assert not enter and reason == "credit_above_ceiling_mostly_intrinsic"


def test_gex_center_distance_cap_keeps_the_center_near_spot():
    """At the old 0.01 this admitted a centre 67 points from spot on a 7457 index. A 0DTE pin bet
    needs the centre reachable in the hours remaining."""
    gex = {"ok": True, "per_strike": [
        {"strike": 7460, "call_gex": 1_000, "put_gex": 0},
        {"strike": 7525, "call_gex": 900_000, "put_gex": 0},   # the wall, 67 points away
    ]}
    snap = snapshot(underlying_price=7457.69, gex=gex)
    p = params("gex", strike_increment=5, max_center_distance_pct=0.003)
    center, reason = engine.select_center(snap, p)
    assert center == 7460 and reason == "max_total_gamma"
    assert abs(center - 7457.69) <= 0.003 * 7457.69


def test_entry_rejects_a_credit_that_cannot_clear_two_fee_stacks():
    """A credit spread that can never produce a risk-free fly has no business being opened inside
    this strategy, however attractive it looks as a standalone vertical."""
    p = params(min_credit_pct_of_width=0.0)
    tiny = snapshot(underlying_price=5998.0,
                    puts={5990: q(1.00, 1.02), 5995: q(1.02, 1.04), 6000: q(1.05, 1.07),
                          6005: q(8.6, 9.0)})
    enter, reason, _ = engine.evaluate_credit_spread_entry(tiny, p, [])
    assert not enter and reason == "credit_cannot_clear_fees"


def test_entry_skips_when_a_leg_has_no_quote():
    bare = snapshot(underlying_price=5998.0, puts={6000: q(5.0, 5.4)})
    enter, reason, _ = engine.evaluate_credit_spread_entry(bare, params(), [])
    assert not enter and reason == "missing_leg_quotes"


# --------------------------------------------------------------------------- legged completion (step 2)
def open_spread(net=2.55, side="put", fees=None):
    return {"kind": "short_vertical", "side": side, "center": 6000, "wing_width": 5,
            "net": net, "quantity": 1, "fees": fly.vertical_open_fee("SPX", 1) if fees is None else fees,
            "status": "open", "position_id": "P1"}


def test_completion_fires_when_the_debit_comes_in_cheap():
    """The Book C mechanism: sold for 2.55, completed for well under that, left holding a fly for a
    net credit — a position whose worst case at expiry is a profit."""
    cheap = snapshot(puts={6000: q(1.0, 1.2), 6005: q(2.4, 2.6)})
    done, reason, plan = engine.evaluate_completion(cheap, open_spread(), params())
    assert done and reason == "ok"
    assert plan["net"] > 0 and plan["floor"] > 0
    assert plan["long_strike"] == 6005


def test_completion_waits_when_the_debit_is_still_close_to_the_credit():
    expensive = snapshot(puts={6000: q(1.0, 1.2), 6005: q(3.5, 3.7)})
    done, reason, _ = engine.evaluate_completion(expensive, open_spread(), params())
    assert not done and reason == "completing_debit_too_high"


def test_completion_refuses_when_fees_would_eat_the_floor():
    """The gate that keeps the module honest.

    `fee_buffer` is expressed in price points, so it does not by itself know what the fee stack costs.
    Set it too low and a completion can clear the price test while producing a fly whose post-fee floor
    is negative — a position that looks risk-free in gross credit and is not. The dollar floor check is
    the backstop, and this is the case that proves it works.

    At two SPX 2-leg fee stacks (~$6.89) a 0.05 net credit is $5: green on price, red in dollars.
    """
    spread = open_spread(net=0.07)
    nearly_free = snapshot(puts={6000: q(1.0, 1.0), 6005: q(1.02, 1.02)})
    done, reason, _ = engine.evaluate_completion(nearly_free, spread, params(fee_buffer=0.02))
    assert not done and reason == "floor_below_minimum_after_fees"


def test_default_fee_buffer_keeps_the_floor_positive_on_spx():
    """The flip side, and the reason the default buffer is 0.10: at one SPX contract, 0.10 points of
    required improvement ($10) already exceeds the two fee stacks (~$6.89), so a completion that
    clears the price gate clears the dollar gate too. Sizing up or moving to a wider grid does not
    preserve that automatically — which is exactly why the dollar check stays in place."""
    assert 0.10 * fly.CONTRACT_MULTIPLIER > fly.vertical_open_fee("SPX", 1) * 2


def test_completion_can_demand_the_guarantee_be_worth_something():
    """A floor of one cent is technically risk-free and practically pointless. `min_floor_dollars`
    lets the operator require the guarantee actually pay for the screen time."""
    cheap = snapshot(puts={6000: q(1.0, 1.2), 6005: q(2.4, 2.6)})
    done, reason, _ = engine.evaluate_completion(cheap, open_spread(), params(min_floor_dollars=500.0))
    assert not done and reason == "floor_below_minimum_after_fees"


def test_completion_uses_the_far_strike_on_the_correct_side():
    """Put flies complete above the centre, call flies below. Coded backwards, the module would
    price a spread that doesn't exist in the snapshot and silently never complete anything."""
    put_snap = snapshot(puts={6000: q(1.0, 1.2), 6005: q(2.4, 2.6)})
    _, _, plan = engine.evaluate_completion(put_snap, open_spread(side="put"), params())
    assert plan["long_strike"] == 6005

    call_snap = snapshot(calls={6000: q(1.0, 1.2), 5995: q(2.4, 2.6)})
    _, _, plan = engine.evaluate_completion(call_snap, open_spread(side="call"), params())
    assert plan["long_strike"] == 5995


def test_completion_ignores_a_position_that_is_already_a_fly():
    already = {**open_spread(), "kind": "fly"}
    done, reason, _ = engine.evaluate_completion(snapshot(), already, params())
    assert not done and reason == "not_a_credit_spread"


# --------------------------------------------------------------------------- outright entry
def cheap_fly_snapshot():
    """A grid where the 5995/6000/6005 call fly prices around 0.30."""
    return snapshot(underlying_price=6002.0,
                    calls={5995: q(7.0, 7.2), 6000: q(4.0, 4.2), 6005: q(1.3, 1.5)})


def test_outright_entry_buys_a_cheap_fly_when_the_book_can_afford_it():
    enter, reason, plan = engine.evaluate_outright_entry(
        cheap_fly_snapshot(), params(), [], realized_cash=500.0)
    assert enter and reason == "ok"
    assert plan["debit"] <= 0.50 and plan["cost"] <= 500.0


def test_outright_entry_rejects_an_expensive_fly():
    """Outright flies are bought deliberately cheap; an expensive one is a different trade entirely."""
    enter, reason, _ = engine.evaluate_outright_entry(
        cheap_fly_snapshot(), params(max_fly_debit=0.10), [], realized_cash=500.0)
    assert not enter and reason == "fly_debit_above_max"


def test_outright_entry_will_not_spend_money_the_book_has_not_taken_in():
    """This is what bounds the funded mode's floor by construction — the book never goes into its own
    pocket to buy a lottery ticket."""
    enter, reason, _ = engine.evaluate_outright_entry(
        cheap_fly_snapshot(), params(), [], realized_cash=5.0)
    assert not enter and reason == "not_funded_by_realized_credit"


def test_outright_entry_rejects_an_implausible_quote():
    """A long fly's value is bounded below by zero, so a non-positive modeled debit is a stale or
    crossed quote, not free money."""
    crossed = snapshot(underlying_price=6002.0,
                       calls={5995: q(1.0, 1.1), 6000: q(4.0, 4.1), 6005: q(1.0, 1.1)})
    enter, reason, _ = engine.evaluate_outright_entry(crossed, params(), [], realized_cash=500.0)
    assert not enter and reason == "implausible_fly_quote"


# --------------------------------------------------------------------------- settlement & stats
def test_settle_marks_pins_and_pnl():
    positions = [
        {"kind": "fly", "side": "put", "center": 6000, "wing_width": 5, "net": 1.05,
         "quantity": 1, "fees": 0.0, "entry_mode": "legged"},
        {"kind": "fly", "side": "put", "center": 6100, "wing_width": 5, "net": -0.20,
         "quantity": 1, "fees": 0.0, "entry_mode": "outright"},
    ]
    settled = engine.settle(positions, 6001.0)
    assert settled[0]["pinned"] is True
    assert settled[0]["pnl"] == pytest.approx(505.0)   # (1.05 + 4.00) * 100
    assert settled[1]["pinned"] is False
    assert settled[1]["pnl"] == pytest.approx(-20.0)


def test_session_stats_report_the_three_numbers_that_matter():
    positions = [
        # a legged entry that completed into a fly
        {"kind": "fly", "side": "put", "center": 6000, "wing_width": 5, "net": 1.05,
         "quantity": 1, "fees": 5.0, "entry_mode": "legged", "status": "settled", "pinned": True},
        # a legged entry that never completed — the branch expected to dominate
        {"kind": "short_vertical", "side": "call", "center": 6050, "wing_width": 5, "net": 2.0,
         "quantity": 1, "fees": 5.0, "entry_mode": "legged", "status": "open"},
    ]
    stats = engine.session_stats(positions)
    assert stats["completion_rate"] == 0.5
    assert stats["risk_free_rate"] == 1.0
    assert stats["pin_rate"] == 1.0
    assert stats["uncompleted_verticals"] == 1
