"""Unit tests for the entry / completion decision engine."""

import pytest

from cherrypick.flies import engine, fly

BASE_CONFIG = {
    "defaults": {
        "wing_width": 5,
        "strike_increment": 5,
        "quantity": 1,
        "max_positions": 4,
        "entry_modes": ["legged", "outright"],
        "min_credit_pct_of_width": 0.20,
        "max_credit_pct_of_width": 0.60,
        "fee_buffer": 0.10,
        "min_floor_dollars": 0.0,
        "max_fly_debit": 0.50,
        "max_center_distance_pct": 0.01,
        "slippage_frac": 0.125,
        "entry_windows": [],
    },
    "arms": {"gex": {}, "time_window": {}, "control": {}},
}


def q(bid, ask):
    return {"bid": bid, "ask": ask}


def snapshot(**over):
    """A 0DTE SPX snapshot with spot at 6000 and a plausible put/call grid around it."""
    snap = {
        "symbol": "SPX",
        "date": "2026-07-20",
        "dte": 0,
        "underlying_price": 6000.0,
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
    gex = {
        "ok": True,
        "per_strike": [
            {"strike": 5990, "call_gex": 1_000, "put_gex": 500, "net_gex": 500},
            {"strike": 6005, "call_gex": 90_000, "put_gex": 88_000, "net_gex": 2_000},
            {"strike": 6010, "call_gex": 9_000, "put_gex": 1_000, "net_gex": 8_000},
        ],
    }
    center, reason = engine.select_center(snapshot(gex=gex), params("gex"))
    assert (center, reason) == (6005.0, "max_total_gamma")


def test_gex_arm_finds_a_strike_where_net_gex_is_negative_everywhere():
    """The case measured on a real SPX chain: put open interest dominates every strike near spot, so
    net GEX is negative across the whole neighbourhood and the old rule had nothing to select."""
    gex = {
        "ok": True,
        "per_strike": [
            {"strike": 5995, "call_gex": 10_000, "put_gex": 200_000, "net_gex": -190_000},
            {"strike": 6005, "call_gex": 40_000, "put_gex": 800_000, "net_gex": -760_000},
        ],
    }
    center, reason = engine.select_center(snapshot(gex=gex), params("gex"))
    assert (center, reason) == (6005.0, "max_total_gamma")


def test_gex_arm_ignores_strikes_beyond_the_distance_cap():
    """A huge GEX pile 3% away is not a 0DTE pin candidate — the cap keeps the arm centred near spot."""
    gex = {
        "ok": True,
        "per_strike": [
            {"strike": 6005, "call_gex": 1_000, "put_gex": 0, "net_gex": 1_000},
            {"strike": 6200, "call_gex": 900_000, "put_gex": 0, "net_gex": 900_000},
        ],
    }
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
        snapshot(now_min=11 * 60 + 10, underlying_price=5998.0), p, []
    )
    assert enter and plan["entry_window"] == "11:00-11:30"


# --------------------------------------------------------------------------- legged entry (step 1)
def test_credit_spread_entry_returns_a_complete_plan():
    enter, reason, plan = engine.evaluate_credit_spread_entry(snapshot(underlying_price=5998.0), params(), [])
    assert enter and reason == "ok"
    assert plan["side"] == "put" and plan["center"] == 6000.0
    # Completing a put fly centred at 6000 means buying the 6005/6000 put debit spread.
    assert plan["completing_strike"] == 6005.0
    assert plan["completing_direction"] == "up"


def test_entry_requires_0dte():
    enter, reason, _ = engine.evaluate_credit_spread_entry(snapshot(dte=1), params(), [])
    assert not enter and reason == "no_0dte_expiration"


# --------------------------------------------------------------------------- per-window cap
WINDOWS = [["11:00", "12:30"], ["12:30", "13:00"]]


def _held(window, n):
    """n open positions already taken in `window`, parked off-centre so only the cap can refuse."""
    return [{"center": 5000.0 + i, "entry_window": window, "status": "open"} for i in range(n)]


def test_per_window_cap_blocks_a_window_that_spent_its_share():
    p = params(entry_windows=WINDOWS, max_positions_per_window=2)
    enter, reason, _ = engine.evaluate_credit_spread_entry(snapshot(), p, _held("11:00-12:30", 2))
    assert not enter and reason == "max_positions_this_window_reached"


def test_per_window_cap_leaves_a_later_window_free():
    """The whole point: a full first window must not consume the later windows' budget, which is how
    15 of 16 entries ended up in one window and the timing hypothesis went untested."""
    p = params(entry_windows=WINDOWS, max_positions_per_window=2)
    # now_min sits inside the SECOND window while the first has already taken its two.
    enter, reason, _ = engine.evaluate_credit_spread_entry(
        snapshot(now_min=12 * 60 + 45), p, _held("11:00-12:30", 2)
    )
    assert enter, reason


def test_global_max_positions_still_binds_across_windows():
    p = params(entry_windows=WINDOWS, max_positions_per_window=4, max_positions=4)
    held = _held("11:00-12:30", 2) + _held("12:30-13:00", 2)
    enter, reason, _ = engine.evaluate_credit_spread_entry(snapshot(now_min=12 * 60 + 45), p, held)
    assert not enter and reason == "max_positions_reached"


def test_per_window_cap_is_off_unless_configured():
    """Existing single-window arms and books entered before the cap existed must be unaffected."""
    p = params(entry_windows=WINDOWS)
    enter, reason, _ = engine.evaluate_credit_spread_entry(snapshot(), p, _held("11:00-12:30", 3))
    assert enter, reason


def test_positions_without_a_window_are_not_counted_against_one():
    p = params(entry_windows=WINDOWS, max_positions_per_window=1)
    legacy = [{"center": 5000.0, "entry_window": None, "status": "open"}]
    enter, reason, _ = engine.evaluate_credit_spread_entry(snapshot(), p, legacy)
    assert enter, reason


# --------------------------------------------------------------------------- post-open blackout
def test_no_entry_before_blocks_the_first_thirty_minutes():
    p = params(no_entry_before="10:00")
    enter, reason, _ = engine.evaluate_credit_spread_entry(snapshot(now_min=9 * 60 + 45), p, [])
    assert not enter and reason == "before_open_gate"


def test_no_entry_before_outranks_an_arm_asking_for_an_earlier_window():
    """The whole point of a floor over per-arm windows: four window lists are four chances to reopen
    the hole. An arm whose window opens at 09:35 must still be refused."""
    p = params(no_entry_before="10:00", entry_windows=[["09:35", "10:30"]])
    enter, reason, _ = engine.evaluate_credit_spread_entry(snapshot(now_min=9 * 60 + 40), p, [])
    assert not enter and reason == "before_open_gate"
    # ...and allowed once past the floor, inside the same window.
    enter, reason, _ = engine.evaluate_credit_spread_entry(snapshot(now_min=10 * 60 + 5), p, [])
    assert enter, reason


def test_no_entry_before_also_gates_outright_entries():
    p = params(no_entry_before="10:00")
    enter, reason, _ = engine.evaluate_outright_entry(snapshot(now_min=9 * 60 + 45), p, [], 5000.0)
    assert not enter and reason == "before_open_gate"


def test_no_entry_before_is_off_when_unset():
    p = params(entry_windows=[["09:35", "10:30"]])
    enter, reason, _ = engine.evaluate_credit_spread_entry(snapshot(now_min=9 * 60 + 40), p, [])
    assert enter, reason


# --------------------------------------------------------------------------- the wide_wing arm
def test_wide_wing_arm_centers_atm_like_control_but_uses_its_own_width():
    """It is control's twin so the pair isolates wing width — same centring, same window, wider wings."""
    cfg = dict(BASE_CONFIG)
    cfg["arms"] = dict(BASE_CONFIG["arms"], wide_wing={"wing_width": 20})
    p = engine.merged_params(cfg, "wide_wing")
    center, reason = engine.select_center(snapshot(underlying_price=6002.0), p)
    assert (center, reason) == (6000.0, "atm")
    assert p["wing_width"] == 20
    assert "wide_wing" in engine.ARMS


def test_width_arms_are_control_twins_sweeping_wing_width():
    """width-2..width-5 pin wing_width to N strike increments; control at the default width is the
    1-increment rung, so no width-1 arm exists (it would duplicate control's book under a new name)."""
    assert "width-1" not in engine.ARMS
    for n in (2, 3, 4, 5):
        arm = f"width-{n}"
        assert arm in engine.ARMS
        cfg = dict(BASE_CONFIG)
        cfg["arms"] = dict(BASE_CONFIG["arms"], **{arm: {"wing_width": n}})
        p = engine.merged_params(cfg, arm)
        center, reason = engine.select_center(snapshot(underlying_price=6002.0), p)
        assert (center, reason) == (6000.0, "atm")
        assert p["wing_width"] == n


def test_entry_respects_the_position_cap():
    open_positions = [{"center": 5000 + i, "kind": "fly"} for i in range(4)]
    enter, reason, _ = engine.evaluate_credit_spread_entry(snapshot(), params(), open_positions)
    assert not enter and reason == "max_positions_reached"


def test_entry_will_not_stack_two_structures_on_one_center():
    """Two flies on the same strike double the pin bet without adding a profit zone — the opposite of
    what a forest of separate zones is for."""
    enter, reason, _ = engine.evaluate_credit_spread_entry(
        snapshot(underlying_price=5998.0), params(), [{"center": 6000.0, "kind": "fly"}]
    )
    assert not enter and reason == "center_already_occupied"


def test_entry_rejects_a_credit_below_the_floor():
    thin = snapshot(
        underlying_price=5998.0,
        puts={5990: q(4.8, 5.2), 5995: q(4.9, 5.3), 6000: q(5.0, 5.4), 6005: q(8.6, 9.0)},
    )
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
        puts={7525: q(70.0, 70.6), 7520: q(65.4, 66.0)},
    )
    # The OLD distance cap, so the gex arm reaches the wall exactly as it did against live quotes.
    p = params("gex", strike_increment=5, wing_width=5, max_center_distance_pct=0.01)
    assert engine.select_center(deep_itm, p)[0] == 7525, "fixture must reproduce the far centre"

    enter, reason, _ = engine.evaluate_credit_spread_entry(deep_itm, p, [])
    assert not enter and reason == "credit_above_ceiling_mostly_intrinsic"


def test_the_ceiling_does_not_block_a_normal_atm_entry():
    """The counterweight — a ceiling that blocked ordinary entries would be worse than no ceiling.
    A real ATM SPX 5-wide priced at 41% of width, comfortably under the 60% cap."""
    enter, reason, plan = engine.evaluate_credit_spread_entry(snapshot(underlying_price=5998.0), params(), [])
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
        puts={7460: q(5.0, 5.4), 7455: q(3.0, 3.4), 7525: q(70.0, 70.6), 7520: q(65.4, 66.0)},
    )

    # 1. Distance cap alone (ceiling disabled): the arm never reaches the far strike.
    tight = params(
        "gex", strike_increment=5, wing_width=5, max_center_distance_pct=0.003, max_credit_pct_of_width=99.0
    )
    enter, _, plan = engine.evaluate_credit_spread_entry(deep_itm, tight, [])
    assert enter and plan["center"] == 7460

    # 2. Ceiling alone (cap loosened back to what shipped): it reaches the strike and is refused.
    loose = params("gex", strike_increment=5, wing_width=5, max_center_distance_pct=0.01)
    enter, reason, _ = engine.evaluate_credit_spread_entry(deep_itm, loose, [])
    assert not enter and reason == "credit_above_ceiling_mostly_intrinsic"


def test_gex_center_distance_cap_keeps_the_center_near_spot():
    """At the old 0.01 this admitted a centre 67 points from spot on a 7457 index. A 0DTE pin bet
    needs the centre reachable in the hours remaining."""
    gex = {
        "ok": True,
        "per_strike": [
            {"strike": 7460, "call_gex": 1_000, "put_gex": 0},
            {"strike": 7525, "call_gex": 900_000, "put_gex": 0},  # the wall, 67 points away
        ],
    }
    snap = snapshot(underlying_price=7457.69, gex=gex)
    p = params("gex", strike_increment=5, max_center_distance_pct=0.003)
    center, reason = engine.select_center(snap, p)
    assert center == 7460 and reason == "max_total_gamma"
    assert abs(center - 7457.69) <= 0.003 * 7457.69


def test_entry_rejects_a_credit_that_cannot_clear_two_fee_stacks():
    """A credit spread that can never produce a risk-free fly has no business being opened inside
    this strategy, however attractive it looks as a standalone vertical."""
    p = params(min_credit_pct_of_width=0.0)
    tiny = snapshot(
        underlying_price=5998.0,
        puts={5990: q(1.00, 1.02), 5995: q(1.02, 1.04), 6000: q(1.05, 1.07), 6005: q(8.6, 9.0)},
    )
    enter, reason, _ = engine.evaluate_credit_spread_entry(tiny, p, [])
    assert not enter and reason == "credit_cannot_clear_fees"


def test_entry_skips_when_a_leg_has_no_quote():
    bare = snapshot(underlying_price=5998.0, puts={6000: q(5.0, 5.4)})
    enter, reason, _ = engine.evaluate_credit_spread_entry(bare, params(), [])
    assert not enter and reason == "missing_leg_quotes"


# --------------------------------------------------------------------------- legged completion (step 2)
def open_spread(net=2.55, side="put", fees=None):
    return {
        "kind": "short_vertical",
        "side": side,
        "center": 6000,
        "wing_width": 5,
        "net": net,
        "quantity": 1,
        "fees": fly.vertical_open_fee("SPX", 1) if fees is None else fees,
        "status": "open",
        "position_id": "P1",
    }


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


def test_completion_floor_matches_position_floor_exactly():
    """Regression (2026-07-30): evaluate_completion used to compute its own floor inline
    (net*100*qty - fees - completion_fee) instead of calling fly.position_floor -- a second,
    driftable formula for the same number. A completion's dollar gate must always agree with
    what `fly.position_floor` says the resulting fly's floor actually is, whatever that function
    currently means -- and it HAS changed twice: it briefly reserved a worst-case
    exercise-assignment fee, stopped when the pre-close ITM exit was introduced to bound that cost,
    then reserved it again on 2026-08-01 when that exit was removed. Routing through the one
    function is what made each of those changes apply here for free instead of silently drifting."""
    spread = open_spread(net=0.30)
    thin = snapshot(puts={6000: q(1.00, 1.00), 6005: q(1.05, 1.05)})  # 0.05 debit, no slippage
    done, reason, plan = engine.evaluate_completion(thin, spread, params())
    assert done and reason == "ok"
    assert plan["floor"] == pytest.approx(
        round(
            fly.position_floor(
                {
                    "kind": "fly",
                    "side": "put",
                    "center": 6000,
                    "wing_width": 5,
                    "net": plan["net"],
                    "quantity": 1,
                    "fees": spread["fees"] + plan["completion_fee"],
                }
            ),
            2,
        )
    )


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


# --------------------------------------------------------------------------- pre-close ITM exit
def open_fly(net=1.0, fees=0.0):
    return {
        "kind": "fly",
        "side": "put",
        "center": 6000,
        "wing_width": 5,
        "net": net,
        "quantity": 1,
        "fees": fees,
    }


def _itm_close_vertical_snapshot(**over):
    base = dict(
        now_min=955,
        underlying_price=5998.0,  # centre (6000, the short leg) ITM; protective long (5995) still OTM
        puts={5995: q(0.0, 0.0), 6000: q(2.0, 2.0)},
    )
    base.update(over)
    return snapshot(**base)


# --------------------------------------------------------------------------- regime tagging (Phase 1c)
def test_classify_regime_baseline_snapshot():
    """The module's own default fixture: a symmetric 6000-centred put/call grid at midday with no
    GEX data cached -- normal vol, flat skew, midday, unknown GEX. A good sanity check that all
    four dimensions read sensibly together, not just in isolation."""
    regime = engine.classify_regime(snapshot(), params())
    assert regime["vol_bucket"] == "normal"
    assert regime["gex_bucket"] == "unknown"
    assert regime["time_bucket"] == "midday"
    assert regime["skew_bucket"] == "flat"
    # Every bucket ships with the measure it came from, so a threshold can be re-derived later
    # rather than re-guessed. An "unknown" bucket carries None -- never a fabricated zero.
    assert regime["vol_value"] is not None and regime["skew_value"] is not None
    assert regime["time_value"] == snapshot()["now_min"]
    assert regime["gex_concentration"] is None
    assert regime["net_gex"] is None and regime["gex_strikes"] is None


def test_trend_bucket_reads_the_session_open():
    p = params()  # default band 20.0 points
    up = snapshot(underlying_price=6106.0, session={"day_open": 6000.0})
    assert engine._classify_trend(up, p) == ("up_from_open", 106.0)
    down = snapshot(underlying_price=5940.0, session={"day_open": 6000.0})
    assert engine._classify_trend(down, p) == ("down_from_open", -60.0)
    # Inside the band the day has not committed; 'flat' rather than rounding noise into a trend.
    flat = snapshot(underlying_price=6003.0, session={"day_open": 6000.0})
    assert engine._classify_trend(flat, p) == ("flat", 3.0)


def test_trend_band_covers_the_measured_dead_zone():
    """The band is 20, not one strike, and this pins why (2026-08-05). Entries opposing a 10-25
    point drift completed 100% of the time -- in that range the read is inverted, not merely weak --
    while past 25 points it is nearly absolute. A 5-point band called +13.68 a trend and got
    2026-08-05 10:01 wrong; 20 calls it 'flat', which is what it was: that session went on to
    reverse and settle 48 points BELOW its open."""
    p = params()
    undecided = snapshot(underlying_price=7785.30, session={"day_open": 7771.62})
    assert engine._classify_trend(undecided, p) == ("flat", pytest.approx(13.68))
    committed = snapshot(underlying_price=7737.50, session={"day_open": 7771.62})
    assert engine._classify_trend(committed, p)[0] == "down_from_open"


def test_trend_band_stays_configurable():
    """Every threshold here is a placeholder. 20 was chosen on 76 rows across 3 sessions, on the
    same rows that measure it, so it has to stay re-derivable rather than baked in."""
    snap = snapshot(underlying_price=6010.0, session={"day_open": 6000.0})
    assert engine._classify_trend(snap, params(regime_trend_points=5.0))[0] == "up_from_open"
    assert engine._classify_trend(snap, params(regime_trend_points=20.0))[0] == "flat"


def test_trend_bucket_unknown_without_a_session_open():
    """Sessions before 2026-07-29 have no summary row, and a missing open is never substituted --
    prev_day_close measures the overnight gap, a different question."""
    p = params()
    assert engine._classify_trend(snapshot(), p) == ("unknown", None)
    assert engine._classify_trend(snapshot(session={"prev_day_close": 5900.0}), p) == (
        "unknown",
        None,
    )
    regime = engine.classify_regime(snapshot(), p)
    assert regime["trend_bucket"] == "unknown" and regime["trend_value"] is None


def test_trend_and_center_offset_flag_the_same_2026_08_04_entry():
    """The session's worked example, pinned so the two dimensions stay comparable: the 14:01 gex
    entry centred 7730 with spot 7736.65 on a day that opened 7630.62. Its centre was behind spot
    AND the day was 106 points up from the open, and it legged into calls needing a DOWN move. A
    trailing-window trend read called that same moment a pullback (-7.8 over 20 minutes), which is
    why this dimension measures against the open instead."""
    p = params()
    snap = snapshot(underlying_price=7736.65, session={"day_open": 7630.62})
    assert engine._classify_trend(snap, p)[0] == "up_from_open"
    assert engine._classify_center_offset(snap, p, 7730.0)[0] == "below_spot"
    assert engine.choose_side(snap, 7730.0) == fly.CALL
    assert fly.completing_side_direction(fly.CALL) == "down"  # opposed the day's direction


def test_the_2026_08_05_mirror_case_on_a_falling_day():
    """The falsification test the centre-lag finding asked for, pinned.

    2026-08-04 was a rising day and every lagging gex centre sat BELOW spot, legging into calls that
    needed a down move. If the mechanism is real rather than an artefact of one up-trending sample,
    a FALLING day must produce the exact mirror: a lagging centre ABOVE spot, legging into puts that
    need an up move. 2026-08-05 opened 7771.62 and settled 7723.55, and it did -- all three gex
    misses were up-completions, both completions were down-completions.

    The 11:50 entry is the one worth pinning: its centre was only +2.5 from spot, INSIDE one strike,
    so `center_offset` reads 'at_spot' and cannot flag it. `trend` catches it instead. That is the
    concrete reason both dimensions are kept -- on this session each caught misses the other missed.
    """
    p = params()
    snap = snapshot(underlying_price=7737.50, session={"day_open": 7771.62})
    assert engine.choose_side(snap, 7740.0) == fly.PUT
    assert fly.completing_side_direction(fly.PUT) == "up"  # needed up on a day that fell
    assert engine._classify_trend(snap, p)[0] == "down_from_open"  # trend flags it
    assert engine._classify_center_offset(snap, p, 7740.0)[0] == "at_spot"  # offset cannot

    # ...and the 10:01 entry is the converse: offset flags it, trend reads 'flat' and cannot.
    early = snapshot(underlying_price=7785.30, session={"day_open": 7771.62})
    assert engine._classify_center_offset(early, p, 7800.0)[0] == "above_spot"
    assert engine._classify_trend(early, p)[0] == "flat"


def test_center_offset_bucket_tracks_the_side_leg_in_will_take():
    """The offset bucket must agree with `choose_side`/`completing_side_direction`, because that
    agreement is the entire reason the dimension exists: a centre below spot legs into CALLS and
    needs a DOWN move, and the whole 2026-08-04 finding is that this is what decides completion.
    If these two ever disagree the tag is describing a trade we did not take."""
    p = params()  # strike_increment 5, so the default offset threshold is one strike
    below = snapshot(underlying_price=6012.0)
    assert engine._classify_center_offset(below, p, 6000.0) == ("below_spot", -12.0)
    assert engine.choose_side(below, 6000.0) == fly.CALL
    assert fly.completing_side_direction(fly.CALL) == "down"

    above = snapshot(underlying_price=5988.0)
    assert engine._classify_center_offset(above, p, 6000.0) == ("above_spot", 12.0)
    assert engine.choose_side(above, 6000.0) == fly.PUT
    assert fly.completing_side_direction(fly.PUT) == "up"


def test_center_offset_at_spot_inside_one_strike_and_unknown_without_a_center():
    p = params()
    # Exactly one strike out is still "at spot" -- the bucket is for a centre the rule could
    # plainly have picked closer, not for ordinary ATM rounding.
    assert engine._classify_center_offset(snapshot(underlying_price=6005.0), p, 6000.0) == (
        "at_spot",
        -5.0,
    )
    # No centre in hand degrades honestly rather than fabricating an offset of zero.
    assert engine._classify_center_offset(snapshot(), p, None) == ("unknown", None)
    regime = engine.classify_regime(snapshot(), p)
    assert regime["center_offset_bucket"] == "unknown"
    assert regime["center_offset_value"] is None


def test_vol_bucket_low_and_high():
    cheap = snapshot(puts={6000: q(0.1, 0.1)}, calls={6000: q(0.1, 0.1)})
    bucket, value = engine._classify_vol(cheap, params())
    assert bucket == "low" and value == pytest.approx(0.2 / 6000)
    rich = snapshot(puts={6000: q(15.0, 15.0)}, calls={6000: q(15.0, 15.0)})
    bucket, value = engine._classify_vol(rich, params())
    assert bucket == "high" and value == pytest.approx(30.0 / 6000)


def test_vol_bucket_unknown_without_atm_quotes():
    bare = snapshot(puts={}, calls={})
    assert engine._classify_vol(bare, params()) == ("unknown", None)


def test_gex_bucket_pinning_vs_thin_vs_unknown():
    concentrated = {"ok": True, "per_strike": [{"strike": 6000, "call_gex": 900_000, "put_gex": 900_000}]}
    bucket, share = engine._classify_gex(snapshot(gex=concentrated), params())
    assert bucket == "pinning" and share == pytest.approx(1.0)

    # Twenty evenly-loaded strikes: the top 3 hold 3/20 = 15%, well under the 60% cut.
    even = {
        "ok": True,
        "per_strike": [
            {"strike": 6000 + 5 * i, "call_gex": 10_000, "put_gex": 10_000} for i in range(-10, 10)
        ],
    }
    bucket, share = engine._classify_gex(snapshot(gex=even), params())
    assert bucket == "thin" and share == pytest.approx(3 / 20)

    assert engine._classify_gex(snapshot(), params()) == ("unknown", None)  # no gex key at all
    assert engine._classify_gex(snapshot(gex={"ok": False}), params()) == ("unknown", None)


def test_gex_concentration_is_windowed_to_near_spot():
    """Regression for the degenerate tag (fixed 2026-08-01). Measuring one strike's share of the
    WHOLE chain -- 109-121 strikes on a real 0DTE surface -- made 'pinning' unreachable in practice:
    entry_gex_bucket came back 'thin' 60 times out of 60. A cluster pinning price at spot must not
    be diluted by gamma 300 points away that has no bearing on it."""
    near_spot_cluster = [
        {"strike": 6000 + 5 * i, "call_gex": 500_000, "put_gex": 500_000} for i in (-1, 0, 1)
    ]
    far_away = [{"strike": 5000 + 5 * i, "call_gex": 400_000, "put_gex": 400_000} for i in range(40)]
    gex = {"ok": True, "per_strike": near_spot_cluster + far_away}

    bucket, share = engine._classify_gex(snapshot(gex=gex), params())
    assert bucket == "pinning" and share == pytest.approx(1.0)  # the far mass is outside the window

    # Widen the window far enough to swallow the distant mass and the same surface reads thin.
    wide = params(regime_gex_window_pct=0.5)
    bucket_wide, share_wide = engine._classify_gex(snapshot(gex=gex), wide)
    assert bucket_wide == "thin" and share_wide < 0.6


def test_time_bucket_open_midday_close_unknown():
    assert engine._classify_time(snapshot(now_min=9 * 60 + 45), params()) == ("open", 9 * 60 + 45)
    assert engine._classify_time(snapshot(now_min=12 * 60), params()) == ("midday", 12 * 60)
    assert engine._classify_time(snapshot(now_min=15 * 60 + 45), params()) == ("close", 15 * 60 + 45)
    bare = dict(snapshot())
    del bare["now_min"]
    assert engine._classify_time(bare, params()) == ("unknown", None)


def test_skew_bucket_put_and_call_and_unknown():
    put_rich = snapshot(puts={5995: q(5.0, 5.2)}, calls={6005: q(1.0, 1.2)})
    bucket, diff = engine._classify_skew(put_rich, params())
    assert bucket == "put_skew" and diff > 0
    call_rich = snapshot(puts={5995: q(1.0, 1.2)}, calls={6005: q(5.0, 5.2)})
    bucket, diff = engine._classify_skew(call_rich, params())
    assert bucket == "call_skew" and diff < 0
    bare = snapshot(puts={}, calls={})
    assert engine._classify_skew(bare, params()) == ("unknown", None)


# --------------------------------------------------------------------------- debit_first (Phase 1)
def debit_snapshot(**over):
    """Spot at the centre (6000) -> choose_debit_side picks CALL. Custom call quotes priced for a
    plausible ~24% of width debit spread (5995/6000)."""
    return snapshot(calls={5995: q(2.0, 2.4), 6000: q(1.0, 1.2)}, **over)


def test_choose_debit_side_inverts_choose_side():
    for spot in (5990.0, 6000.0, 6010.0):
        snap = snapshot(underlying_price=spot)
        assert engine.choose_debit_side(snap, 6000.0) != engine.choose_side(snap, 6000.0)


def test_debit_vertical_entry_returns_a_complete_plan():
    enter, reason, plan = engine.evaluate_debit_vertical_entry(debit_snapshot(), params(), [])
    assert enter and reason == "ok"
    assert plan["side"] == "call" and plan["center"] == 6000.0
    assert 0.0 < plan["debit"] < plan["wing_width"]
    assert plan["completing_direction"] == "up"  # CALL side completes on spot rising to centre


def test_debit_vertical_entry_requires_0dte():
    enter, reason, _ = engine.evaluate_debit_vertical_entry(debit_snapshot(dte=1), params(), [])
    assert not enter and reason == "no_0dte_expiration"


def test_debit_vertical_entry_rejects_a_debit_below_the_floor():
    thin = snapshot(calls={5995: q(0.05, 0.07), 6000: q(0.0, 0.02)})
    enter, reason, _ = engine.evaluate_debit_vertical_entry(thin, params(), [])
    assert not enter and reason == "debit_below_floor_completion_implausible"


def test_debit_vertical_entry_rejects_an_intrinsic_heavy_debit():
    rich = snapshot(calls={5995: q(4.0, 4.2), 6000: q(0.9, 1.0)})
    enter, reason, _ = engine.evaluate_debit_vertical_entry(rich, params(), [])
    assert not enter and reason == "debit_above_ceiling_mostly_intrinsic"


def test_debit_vertical_entry_refuses_when_the_debit_leaves_no_room_to_be_out_earned():
    """The completing credit can never exceed `width`, so a debit that (with buffer + fees) already
    reaches the width is a mathematically dead end."""
    at_width = snapshot(calls={5995: q(5.4, 5.4), 6000: q(0.0, 0.0)})
    enter, reason, _ = engine.evaluate_debit_vertical_entry(at_width, params(max_debit_pct_of_width=99.0), [])
    assert not enter and reason == "debit_cannot_be_out_earned"


def test_debit_vertical_entry_rejects_an_implausible_quote():
    # The lower/cheaper strike (5995) priced BELOW the higher one (6000) -- a stale or crossed
    # quote, since a call debit spread's value can never legitimately be negative.
    crossed = snapshot(calls={5995: q(0.5, 0.6), 6000: q(1.0, 1.1)})
    enter, reason, _ = engine.evaluate_debit_vertical_entry(crossed, params(), [])
    assert not enter and reason == "implausible_debit_quote"


def open_debit_vertical(debit=1.175, side="call", fees=None):
    return {
        "kind": "long_vertical",
        "side": side,
        "center": 6000,
        "wing_width": 5,
        "net": -debit,
        "quantity": 1,
        "fees": fly.vertical_open_fee("SPX", 1) if fees is None else fees,
        "status": "open",
        "position_id": "D1",
    }


def test_debit_completion_fires_when_the_credit_richens_enough():
    """The mirror of test_completion_fires_when_the_debit_comes_in_cheap: spot has drifted toward the
    centre, richening the completing credit spread (short 6000 call / long 6005 call) past the debit
    already paid."""
    rich = snapshot(calls={6000: q(3.0, 3.2), 6005: q(0.5, 0.6)})
    done, reason, plan = engine.evaluate_debit_completion(rich, open_debit_vertical(), params())
    assert done and reason == "ok"
    assert plan["net"] > 0 and plan["floor"] > 0
    assert plan["wing_strike"] == 6005


def test_debit_completion_waits_when_the_credit_is_still_thin():
    thin = snapshot(calls={6000: q(1.0, 1.1), 6005: q(0.7, 0.8)})
    done, reason, _ = engine.evaluate_debit_completion(thin, open_debit_vertical(), params())
    assert not done and reason == "completing_credit_too_low"


def test_debit_completion_ignores_a_position_that_is_not_a_long_vertical():
    done, reason, plan = engine.evaluate_debit_completion(debit_snapshot(), open_spread(), params())
    assert not done and reason == "not_a_debit_vertical" and plan is None


def test_debit_completion_floor_matches_position_floor_exactly():
    """Same discipline as legged's completion: the dollar gate must route through fly.position_floor
    rather than a second, driftable formula."""
    spread = open_debit_vertical(debit=0.30)
    rich = snapshot(calls={6000: q(2.00, 2.00), 6005: q(1.05, 1.05)})  # cheap/no-slippage credit
    done, reason, plan = engine.evaluate_debit_completion(rich, spread, params())
    assert done and reason == "ok"
    assert plan["floor"] == pytest.approx(
        round(
            fly.position_floor(
                {
                    "kind": "fly",
                    "side": "call",
                    "center": 6000,
                    "wing_width": 5,
                    "net": plan["net"],
                    "quantity": 1,
                    "fees": spread["fees"] + plan["completion_fee"],
                }
            ),
            2,
        )
    )


def test_settle_sign_is_not_flipped_for_an_uncompleted_long_vertical():
    """Regression: before _expiry_payoff's explicit dispatch, settle() priced anything that wasn't a
    fly as a short vertical -- for a long vertical (whose worst case is 0, best case +W) that is
    sign-flipped. Deep ITM through both legs must settle at +wing_width, never -wing_width."""
    positions = [{**open_debit_vertical(debit=1.0, fees=0.0), "entry_mode": "debit_first"}]
    settled = engine.settle(positions, 6010.0)  # deep ITM through both call legs
    assert settled[0]["expiry_payoff"] == pytest.approx(5.0)
    # (net + payoff) * 100 = (-1.0 + 5.0) * 100 = 400, less the 2-ITM-strike assignment fee
    assert settled[0]["pnl"] == pytest.approx(400.0 - fly.expire_fee(2))


def _itm_close_debit_vertical_snapshot(**over):
    base = {
        "now_min": 15 * 60 + 55,
        "underlying_price": 6009.0,  # deep ITM through both call legs
        "calls": {5995: q(14.9, 15.0), 6000: q(9.9, 10.0)},
    }
    base.update(over)
    return snapshot(**base)


# --------------------------------------------------------------------------- iron completion (Phase 1b)
def test_iron_completion_fires_when_the_opposite_credit_richens_enough():
    """A held put spread (open_spread, net 2.55) completes into an iron fly by SELLING the call
    spread at the same centre, once that call spread has richened past the width+buffer gate."""
    rich_calls = snapshot(calls={6000: q(4.0, 4.2), 6005: q(0.5, 0.6)})
    done, reason, plan = engine.evaluate_iron_completion(rich_calls, open_spread(), params())
    assert done and reason == "ok"
    assert plan["net"] > 0 and plan["floor"] > 0
    assert plan["opposite_side"] == "call" and plan["opposite_wing"] == 6005.0


def test_iron_completion_waits_when_the_opposite_credit_is_still_thin():
    """The module's own real quotes: a 6000/6005 call spread priced at 2.3, against a gate of
    2.55 (width 5 + buffer 0.1 - the 2.55 credit already collected) -- not rich enough yet."""
    done, reason, _ = engine.evaluate_iron_completion(snapshot(), open_spread(), params())
    assert not done and reason == "iron_credit_too_low"


def test_iron_completion_ignores_a_position_that_is_not_a_short_vertical():
    done, reason, plan = engine.evaluate_iron_completion(snapshot(), open_debit_vertical(), params())
    assert not done and reason == "not_a_credit_spread" and plan is None


def test_iron_completion_refuses_without_the_opposite_types_leg_quotes():
    bare = snapshot(calls={})
    done, reason, plan = engine.evaluate_iron_completion(bare, open_spread(), params())
    assert not done and reason == "missing_leg_quotes" and plan is None


def test_iron_completion_floor_matches_position_floor_exactly():
    rich_calls = snapshot(calls={6000: q(4.0, 4.2), 6005: q(0.5, 0.6)})
    spread = open_spread(net=2.55)
    done, reason, plan = engine.evaluate_iron_completion(rich_calls, spread, params())
    assert done and reason == "ok"
    assert plan["floor"] == pytest.approx(
        round(
            fly.position_floor(
                {
                    "kind": "iron_fly",
                    "center": 6000,
                    "wing_width": 5,
                    "net": plan["net"],
                    "quantity": 1,
                    "fees": spread["fees"] + plan["completion_fee"],
                }
            ),
            2,
        )
    )


def test_iron_fly_settles_correctly_across_all_three_zones():
    """Regression covering the same sign-flip hazard as debit_first's settle test, for the newest
    kind: deep past either wing must settle at -wing_width, never mispriced as a same-type fly's 0
    floor or a short vertical's -wing_width-only-one-side shape."""
    pos = {
        "kind": "iron_fly",
        "center": 6000,
        "wing_width": 5,
        "net": 6.00,
        "quantity": 1,
        "fees": 0.0,
        "entry_mode": "legged",
    }
    settled = engine.settle([pos], 6000.0)
    assert settled[0]["expiry_payoff"] == pytest.approx(0.0)  # peaks at 0, not +width
    settled = engine.settle([pos], 5990.0)
    assert settled[0]["expiry_payoff"] == pytest.approx(-5.0)
    settled = engine.settle([pos], 6010.0)
    assert settled[0]["expiry_payoff"] == pytest.approx(-5.0)


# --------------------------------------------------------------------------- bwb_roll (Phase 2)
def bwb_snapshot(**over):
    """Spot at 6000 -> choose_side picks PUT -> near wing 6005, far wing (ratio 2.0) 5990. Custom
    put quotes priced for a plausible ~$1.10 net credit (tail = far_width - wing_width = 5)."""
    return snapshot(
        puts={5990: q(0.4, 0.6), 6000: q(1.9, 2.1), 6005: q(2.2, 2.4)},
        **over,
    )


def test_bwb_entry_returns_a_complete_plan():
    enter, reason, plan = engine.evaluate_bwb_entry(bwb_snapshot(), params(max_bwb_tail_dollars=1000), [])
    assert enter and reason == "ok"
    assert plan["side"] == "put" and plan["center"] == 6000.0
    assert plan["wing_width"] == 5 and plan["far_width"] == 10.0  # ratio 2.0 * wing_width
    assert 0.0 < plan["credit"] < plan["far_width"] - plan["wing_width"]


def test_bwb_entry_requires_0dte():
    enter, reason, _ = engine.evaluate_bwb_entry(bwb_snapshot(dte=1), params(), [])
    assert not enter and reason == "no_0dte_expiration"


def test_bwb_entry_rejects_far_width_not_wider_than_wing():
    enter, reason, _ = engine.evaluate_bwb_entry(bwb_snapshot(), params(bwb_far_width_ratio=1.0), [])
    assert not enter and reason == "far_width_not_wider_than_wing"


def test_bwb_entry_rejects_a_credit_below_the_tail_floor():
    thin = snapshot(puts={5990: q(1.0, 1.0), 6000: q(1.05, 1.05), 6005: q(1.1, 1.1)})
    enter, reason, _ = engine.evaluate_bwb_entry(thin, params(max_bwb_tail_dollars=1000), [])
    assert not enter and reason == "bwb_credit_below_floor"


def test_bwb_entry_rejects_an_intrinsic_heavy_credit():
    rich = snapshot(puts={5990: q(0.1, 0.1), 6000: q(4.0, 4.0), 6005: q(0.2, 0.2)})
    enter, reason, _ = engine.evaluate_bwb_entry(rich, params(max_bwb_tail_dollars=1000), [])
    assert not enter and reason == "bwb_credit_above_ceiling_mostly_intrinsic"


def test_bwb_entry_rejects_tail_risk_above_max():
    enter, reason, _ = engine.evaluate_bwb_entry(bwb_snapshot(), params(max_bwb_tail_dollars=1.0), [])
    assert not enter and reason == "bwb_tail_risk_above_max"


def open_bwb(credit=1.1, side="put", far_width=10.0, fees=None):
    return {
        "kind": "bwb",
        "side": side,
        "center": 6000,
        "wing_width": 5,
        "far_width": far_width,
        "net": credit,
        "quantity": 1,
        "fees": fly.fly_open_fee("SPX", 1) if fees is None else fees,
        "status": "open",
        "position_id": "B1",
    }


def test_roll_fires_when_the_roll_debit_is_cheap_enough():
    cheap_roll = snapshot(puts={5990: q(4.8, 5.0), 6005: q(5.0, 5.2)})
    done, reason, plan = engine.evaluate_roll(cheap_roll, open_bwb(), params())
    assert done and reason == "ok"
    assert plan["net"] > 0 and plan["floor"] > 0
    assert plan["near_wing"] == 6005.0


def test_roll_waits_when_the_roll_debit_is_still_high():
    expensive_roll = snapshot(puts={5990: q(0.1, 0.2), 6005: q(5.0, 5.2)})
    done, reason, _ = engine.evaluate_roll(expensive_roll, open_bwb(), params())
    assert not done and reason == "roll_debit_too_high"


def test_roll_ignores_a_position_that_is_not_a_bwb():
    done, reason, plan = engine.evaluate_roll(bwb_snapshot(), open_spread(), params())
    assert not done and reason == "not_a_bwb" and plan is None


def test_settle_bwb_across_near_centre_and_tail():
    """Regression covering the settle-time sign-flip hazard for the newest, most asymmetric kind:
    an unrolled bwb settling past its tail must show a NEGATIVE expiry payoff with all 4 contracts
    ITM, never mispriced as bounded (a fly) or as a 2-leg vertical's -wing_width-only shape."""
    pos = {**open_bwb(), "entry_mode": "bwb_roll"}
    settled = engine.settle([pos], 6000.0)
    assert settled[0]["expiry_payoff"] == pytest.approx(5.0)  # peak, at centre
    assert settled[0]["pinned"] is False  # pin is a symmetric-fly concept; never true for a bwb
    settled = engine.settle([pos], 5980.0)  # deep past the far wing
    assert settled[0]["expiry_payoff"] == pytest.approx(-5.0)  # tail = wing_width - far_width
    assert settled[0]["itm_legs"] == 3  # 3 distinct strikes, not 4 contracts


# --------------------------------------------------------------------------- outright entry
def cheap_fly_snapshot():
    """A grid where the 5995/6000/6005 call fly prices around 0.30."""
    return snapshot(underlying_price=6002.0, calls={5995: q(7.0, 7.2), 6000: q(4.0, 4.2), 6005: q(1.3, 1.5)})


def test_outright_entry_buys_a_cheap_fly_when_the_book_can_afford_it():
    enter, reason, plan = engine.evaluate_outright_entry(
        cheap_fly_snapshot(), params(), [], realized_cash=500.0
    )
    assert enter and reason == "ok"
    assert plan["debit"] <= 0.50 and plan["cost"] <= 500.0


def test_outright_entry_rejects_an_expensive_fly():
    """Outright flies are bought deliberately cheap; an expensive one is a different trade entirely."""
    enter, reason, _ = engine.evaluate_outright_entry(
        cheap_fly_snapshot(), params(max_fly_debit=0.10), [], realized_cash=500.0
    )
    assert not enter and reason == "fly_debit_above_max"


def test_outright_entry_will_not_spend_money_the_book_has_not_taken_in():
    """This is what bounds the funded mode's floor by construction — the book never goes into its own
    pocket to buy a lottery ticket."""
    enter, reason, _ = engine.evaluate_outright_entry(cheap_fly_snapshot(), params(), [], realized_cash=5.0)
    assert not enter and reason == "not_funded_by_realized_credit"


def test_outright_entry_rejects_an_implausible_quote():
    """A long fly's value is bounded below by zero, so a non-positive modeled debit is a stale or
    crossed quote, not free money."""
    crossed = snapshot(
        underlying_price=6002.0, calls={5995: q(1.0, 1.1), 6000: q(4.0, 4.1), 6005: q(1.0, 1.1)}
    )
    enter, reason, _ = engine.evaluate_outright_entry(crossed, params(), [], realized_cash=500.0)
    assert not enter and reason == "implausible_fly_quote"


# --------------------------------------------------------------------------- settlement & stats
def test_settle_marks_pins_and_pnl():
    positions = [
        {
            "kind": "fly",
            "side": "put",
            "center": 6000,
            "wing_width": 5,
            "net": 1.05,
            "quantity": 1,
            "fees": 0.0,
            "entry_mode": "legged",
        },
        {
            "kind": "fly",
            "side": "put",
            "center": 6100,
            "wing_width": 5,
            "net": -0.20,
            "quantity": 1,
            "fees": 0.0,
            "entry_mode": "outright",
        },
    ]
    settled = engine.settle(positions, 6001.0)
    assert settled[0]["pinned"] is True
    # (1.05 + 4.00) * 100 = 505, less a $5 exercise fee (only the upper wing, 6005, is ITM at 6001)
    assert settled[0]["itm_legs"] == 1
    assert settled[0]["fees"] == pytest.approx(5.0)
    assert settled[0]["pnl"] == pytest.approx(500.0)
    assert settled[1]["pinned"] is False
    # -0.20 * 100 = -20, less a $15 exercise fee (6001 is beyond every strike of the 6100 fly, so
    # all 3 STRIKES settle ITM -- the doubled centre is one symbol charged once, not two)
    assert settled[1]["itm_legs"] == 3
    assert settled[1]["fees"] == pytest.approx(15.0)
    assert settled[1]["pnl"] == pytest.approx(-35.0)


def test_settle_raises_on_unknown_kind():
    """Regression: settle() used to compute expiry_payoff with an inline fly/else ternary that
    would price any new kind as a short vertical -- sign-flipped for anything whose worst case
    sits above zero. _expiry_payoff must raise instead of silently guessing."""
    positions = [
        {
            "kind": "garbage",
            "side": "put",
            "center": 6000,
            "wing_width": 5,
            "net": 1.05,
            "quantity": 1,
            "fees": 0.0,
            "entry_mode": "legged",
        }
    ]
    with pytest.raises(ValueError, match="unknown position kind"):
        engine.settle(positions, 6001.0)


def test_session_stats_report_the_three_numbers_that_matter():
    positions = [
        # a legged entry that completed into a fly
        {
            "kind": "fly",
            "side": "put",
            "center": 6000,
            "wing_width": 5,
            "net": 1.05,
            "quantity": 1,
            "fees": 5.0,
            "entry_mode": "legged",
            "status": "settled",
            "pinned": True,
        },
        # a legged entry that never completed — the branch expected to dominate
        {
            "kind": "short_vertical",
            "side": "call",
            "center": 6050,
            "wing_width": 5,
            "net": 2.0,
            "quantity": 1,
            "fees": 5.0,
            "entry_mode": "legged",
            "status": "open",
        },
    ]
    stats = engine.session_stats(positions)
    assert stats["completion_rate"] == 0.5
    assert stats["risk_free_rate"] == 1.0
    assert stats["pin_rate"] == 1.0
    assert stats["uncompleted_verticals"] == 1
