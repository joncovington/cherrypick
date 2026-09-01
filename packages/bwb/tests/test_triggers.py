from cherrypick.bwb import triggers

PARAMS = {"delta_trigger": 0.50, "bounce_pullback": 0.05, "flip_buffer": 1.001}


# --------------------------------------------------------------------------- peak / latch updates
def test_update_peak_advances_only_on_measured_tick():
    assert triggers.update_peak(None, 0.30) == 0.30
    assert triggers.update_peak(0.30, None) == 0.30  # unmeasured tick: unchanged
    assert triggers.update_peak(0.30, 0.45) == 0.45
    assert triggers.update_peak(0.45, 0.20) == 0.45  # never regresses


def test_update_below_flip_latches_and_never_unlatches():
    assert triggers.update_below_flip(False, spot=100, gamma_flip=105) is True
    assert triggers.update_below_flip(True, spot=200, gamma_flip=105) is True  # already latched
    assert triggers.update_below_flip(False, spot=110, gamma_flip=105) is False  # above flip
    assert triggers.update_below_flip(False, spot=None, gamma_flip=105) is False  # unmeasured


# --------------------------------------------------------------------------- delta
def test_delta_fires_at_or_above_threshold():
    assert triggers.delta_fires(0.50, PARAMS) is True
    assert triggers.delta_fires(0.55, PARAMS) is True
    assert triggers.delta_fires(0.49, PARAMS) is False
    assert triggers.delta_fires(None, PARAMS) is False


# --------------------------------------------------------------------------- bounce
def test_bounce_fires_on_confirmed_pullback():
    # peak reached 0.50, current pulled back to 0.45 (delta_trigger - bounce_pullback)
    assert triggers.bounce_fires(0.50, 0.45, PARAMS) is True
    assert triggers.bounce_fires(0.52, 0.40, PARAMS) is True


def test_bounce_does_not_fire_without_a_qualifying_peak():
    assert triggers.bounce_fires(0.40, 0.30, PARAMS) is False  # never reached delta_trigger


def test_bounce_does_not_fire_without_a_pullback():
    assert triggers.bounce_fires(0.55, 0.55, PARAMS) is False  # peak met but no pullback yet


def test_bounce_degenerates_to_delta_at_zero_pullback():
    """The property the plan is built on: bounce_pullback == 0 makes the bounce bar's floor and
    ceiling coincide at `delta_trigger` exactly, so on the sampled tick that FIRST touches the
    threshold (current == peak == delta_trigger, the realistic first-crossing case on a discrete
    series) bounce and delta agree byte-for-byte -- the one drift the derived bar cannot prevent.
    They diverge past that single touch tick (bounce demands a pullback BACK to the bar; delta
    only demands having reached it), which is bounce's whole point once pullback is nonzero."""
    zero_pullback = {**PARAMS, "bounce_pullback": 0.0}
    trigger = zero_pullback["delta_trigger"]
    bounce = triggers.bounce_fires(trigger, trigger, zero_pullback)
    delta = triggers.delta_fires(trigger, zero_pullback)
    assert bounce is True
    assert delta is True
    assert bounce == delta

    # Below the bar, both agree it hasn't fired.
    below = trigger - 0.05
    assert triggers.bounce_fires(below, below, zero_pullback) is False
    assert triggers.delta_fires(below, zero_pullback) is False


# --------------------------------------------------------------------------- flip
def test_flip_fires_on_reclaim_above_buffer():
    assert triggers.flip_fires(True, spot=105.2, gamma_flip=105.0, params=PARAMS) is True  # 105*1.001=105.105


def test_flip_does_not_fire_on_knife_edge_reclaim_within_buffer():
    # Exactly at gamma_flip (no buffer cleared) must NOT count as a reclaim.
    assert triggers.flip_fires(True, spot=105.0, gamma_flip=105.0, params=PARAMS) is False


def test_flip_buffer_guard_a_buffer_at_or_below_one_would_admit_a_knife_edge():
    """Guards the config-lint invariant: flip_buffer must be > 1.0, or a bare touch (spot ==
    gamma_flip) would count as a reclaim -- the exact failure the buffer exists to prevent."""
    broken_buffer = {**PARAMS, "flip_buffer": 1.0}
    # At the broken buffer, spot exactly AT gamma_flip now reads as a reclaim -- proving the guard
    # matters (a real config never ships flip_buffer <= 1.0; this is why not).
    assert triggers.flip_fires(True, spot=105.0, gamma_flip=105.0, params=broken_buffer) is True


def test_flip_never_fires_without_the_below_flip_latch():
    assert triggers.flip_fires(False, spot=200.0, gamma_flip=105.0, params=PARAMS) is False


def test_flip_never_fires_on_unmeasured_tick():
    assert triggers.flip_fires(True, spot=None, gamma_flip=105.0, params=PARAMS) is False
    assert triggers.flip_fires(True, spot=105.2, gamma_flip=None, params=PARAMS) is False


# --------------------------------------------------------------------------- evaluate / derivation
def test_evaluate_control_never_fires():
    state = {"peak_abs_delta": 0.60, "below_flip_seen": True}
    tick = {"abs_delta": 0.60, "spot": 200.0, "gamma_flip": 105.0}
    result = triggers.evaluate("control", state, tick, PARAMS)
    assert result["fired"] is False


def test_evaluate_updates_latches_regardless_of_book():
    """The counterfactual-on-control property: every book's evaluate() call updates the SAME
    latches from the SAME tick, so control's own rows carry what would have fired on another
    book's trigger."""
    state = {"peak_abs_delta": 0.30, "below_flip_seen": False}
    tick = {"abs_delta": 0.55, "spot": 100.0, "gamma_flip": 105.0}
    result = triggers.evaluate("control", state, tick, PARAMS)
    assert result["peak_abs_delta"] == 0.55
    assert result["below_flip_seen"] is True


def test_derive_latches_from_ticks_matches_incremental_updates():
    ticks = [
        {"abs_delta": 0.20, "spot": 200.0, "gamma_flip": 105.0},
        {"abs_delta": 0.40, "spot": 110.0, "gamma_flip": 105.0},
        {"abs_delta": 0.55, "spot": 100.0, "gamma_flip": 105.0},  # below flip here
        {"abs_delta": 0.45, "spot": 106.0, "gamma_flip": 105.0},  # reclaimed
    ]
    derived = triggers.derive_latches_from_ticks(ticks)

    # Replay the same ticks incrementally, exactly as the loop would persist them tick by tick.
    peak = None
    below_flip = False
    for t in ticks:
        peak = triggers.update_peak(peak, t["abs_delta"])
        below_flip = triggers.update_below_flip(below_flip, t["spot"], t["gamma_flip"])

    assert derived == {"peak_abs_delta": peak, "below_flip_seen": below_flip}
    assert derived["peak_abs_delta"] == 0.55
    assert derived["below_flip_seen"] is True


def test_derive_latches_from_ticks_skips_unmeasured_entries():
    ticks = [
        {"abs_delta": 0.30, "spot": 200.0, "gamma_flip": 105.0},
        {"abs_delta": None, "spot": None, "gamma_flip": None},  # unmeasured -- must not disturb state
        {"abs_delta": 0.20, "spot": 200.0, "gamma_flip": 105.0},
    ]
    derived = triggers.derive_latches_from_ticks(ticks)
    assert derived["peak_abs_delta"] == 0.30  # never regressed by the later 0.20 or the gap
    assert derived["below_flip_seen"] is False
