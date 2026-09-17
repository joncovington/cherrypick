from cherrypick.bwb import engine, management

PARAMS = {**management.PARAM_DEFAULTS, "book": "delta"}


def _position(**overrides):
    base = {"book": "delta", "advice_params": None, "armed_at": None, "addon_fired_at": None}
    base.update(overrides)
    return base


def test_hold_when_not_triggered():
    position = _position()
    decision, latches = management.evaluate(
        position,
        PARAMS,
        trigger_state={},
        tick={"abs_delta": 0.10, "spot": 100.0, "gamma_flip": 90.0},
        addon_credit=None,
    )
    assert decision.action == "hold"
    assert decision.reason == "not_triggered"


def test_arms_when_trigger_fires():
    position = _position()
    decision, latches = management.evaluate(
        position,
        PARAMS,
        trigger_state={},
        tick={"abs_delta": 0.55, "spot": 100.0, "gamma_flip": 90.0},
        addon_credit=None,
    )
    assert decision.action == "arm"
    assert decision.reason == "delta_trigger_met"
    assert latches["peak_abs_delta"] == 0.55


def test_holds_armed_position_when_addon_unpriced():
    position = _position(armed_at="2026-09-01T10:00:00-04:00")
    decision, _ = management.evaluate(
        position,
        PARAMS,
        trigger_state={"peak_abs_delta": 0.55},
        tick={"abs_delta": 0.55, "spot": 100.0, "gamma_flip": 90.0},
        addon_credit=None,
    )
    assert decision.action == "hold"
    assert decision.reason == "addon_unpriced"


def test_holds_armed_position_when_addon_not_credit():
    position = _position(armed_at="2026-09-01T10:00:00-04:00")
    decision, _ = management.evaluate(
        position,
        PARAMS,
        trigger_state={"peak_abs_delta": 0.55},
        tick={"abs_delta": 0.55, "spot": 100.0, "gamma_flip": 90.0},
        addon_credit=-0.05,
    )
    assert decision.action == "hold"
    assert decision.reason == "addon_not_credit"


def test_fires_addon_when_credit_clears_floor():
    position = _position(armed_at="2026-09-01T10:00:00-04:00")
    decision, _ = management.evaluate(
        position,
        PARAMS,
        trigger_state={"peak_abs_delta": 0.55},
        tick={"abs_delta": 0.55, "spot": 100.0, "gamma_flip": 90.0},
        addon_credit=0.15,
    )
    assert decision.action == "fire_addon"
    assert decision.detail["credit"] == 0.15


def test_addon_already_fired_never_refires():
    position = _position(armed_at="2026-09-01T10:00:00-04:00", addon_fired_at="2026-09-01T10:05:00-04:00")
    decision, _ = management.evaluate(
        position,
        PARAMS,
        trigger_state={"peak_abs_delta": 0.55},
        tick={"abs_delta": 0.55, "spot": 100.0, "gamma_flip": 90.0},
        addon_credit=5.0,  # even a huge credit must not re-fire
    )
    assert decision.action == "hold"
    assert decision.reason == "addon_already_fired"


def test_control_book_never_arms():
    control_params = {**management.PARAM_DEFAULTS, "book": "control"}
    position = _position(book="control")
    decision, _ = management.evaluate(
        position,
        control_params,
        trigger_state={},
        tick={"abs_delta": 0.99, "spot": 1.0, "gamma_flip": 1.0},
        addon_credit=None,
    )
    assert decision.action == "hold"
    assert decision.reason == "not_triggered"


def test_effective_params_untouched_for_control():
    position = _position(book="control")
    config = {"defaults": {"delta_trigger": 0.55}, "books": {"control": {}}}
    params = management.effective_params(position, config)
    assert params["delta_trigger"] == 0.55
    assert params["book"] == "control"


def test_effective_params_overlays_advised():
    position = {"book": "advised:delta", "advice_params": '{"delta_trigger": 0.35}'}
    config = {"defaults": {"delta_trigger": 0.50}, "books": {"delta": {}}}
    params = management.effective_params(position, config)
    assert params["delta_trigger"] == 0.35


def test_execution_gate_unusable_mark():
    assert management.execution_gate({"ok": False}, PARAMS, now=None) == "unusable_mark"


def test_execution_gate_spread_too_wide():
    snap = {"ok": True, "max_spread_pct": 0.9}
    assert (
        management.execution_gate(snap, {**PARAMS, "max_leg_spread_pct": 0.25}, now=None) == "spread_too_wide"
    )


def test_execution_gate_clear():
    snap = {"ok": True, "max_spread_pct": 0.1}
    assert management.execution_gate(snap, {**PARAMS, "max_leg_spread_pct": 0.25}, now=None) is None


def test_frozen_params_govern_an_open_advised_position_after_the_artifact_expires():
    """The exit-continuity rule, pinned (2026-09-12). Advice is single-session and expiring for
    ENTRIES: an expired artifact admits nothing. But a position an earlier session opened under
    admitted params carries them frozen on its row, and `effective_params` reads that stamp back
    every tick -- advice lapsing mid-hold must never hand an open position to rules nobody chose."""
    import json

    from cherrypick.core import advice as core_advice

    bounds = {"delta_trigger": {"min": 0.2, "max": 0.6}}
    expired = {
        "module": "bwb",
        "session": "2026-09-11",
        "expires_at": "2026-09-11T23:59:59-04:00",
        "proposals": [{"param": "delta_trigger", "value": 0.35, "rationale": "r"}],
    }
    assert core_advice.validate(expired, bounds, "2026-09-11")["ok"] is False, "no new entries on it"

    position = {"book": "advised:control", "advice_params": json.dumps({"delta_trigger": 0.35})}
    config = {"defaults": {"delta_trigger": 0.50}, "books": {"control": {}}}
    assert management.effective_params(position, config)["delta_trigger"] == 0.35


# --------------------------- one advised book per experiment (2026-09-17)


def test_base_book_resolves_an_experiment_tag_through_the_decision_then_the_config():
    """Since 2026-09-17 an advised tag names the EXPERIMENT, not the base. The decision entry
    answers first, the configured `advice.base_book` answers for a row whose decision is gone, and
    the legacy `advised:<base>` tag still reads its base straight off the name."""
    decision = {
        "experiments": [
            {"experiment_id": "exp-1", "tag": "advised:early-delta", "base": "delta", "params": {"a": 1}}
        ]
    }
    assert engine.base_book("advised:early-delta", decision=decision) == "delta"
    assert engine.base_book("advised:early-delta") == "control"
    assert engine.base_book("advised:early-delta", config={"advice": {"base_book": "bounce"}}) == "bounce"
    assert engine.base_book("advised:delta", config={"advice": {"base_book": "bounce"}}) == "delta"
    assert engine.base_book("advised:wall", config={"books": {"wall": {"enabled": True}}}) == "wall"
    assert engine.base_book("flip") == "flip"


def test_effective_params_resolve_an_experiment_tag_to_the_configured_base():
    config = {"defaults": {"delta_trigger": 0.50}, "books": {"control": {}, "delta": {"delta_trigger": 0.45}}}
    row = {"book": "advised:early-delta", "advice_params": '{"delta_trigger": 0.35}'}
    params = management.effective_params(row, config)
    assert params["base_book"] == "control"
    assert params["delta_trigger"] == 0.35
    config["advice"] = {"base_book": "delta"}
    params = management.effective_params({"book": "advised:early-delta", "advice_params": "{}"}, config)
    assert params["base_book"] == "delta" and params["delta_trigger"] == 0.45


def test_an_experiment_twin_arms_under_its_base_books_trigger_not_its_tags():
    """`advised:early-delta` shadowing `delta` arms on a delta touch; the same tag shadowing
    `control` never arms -- the verdict reads the resolved base, never the tag."""
    tick = {"abs_delta": 0.99, "spot": 1.0, "gamma_flip": 1.0}
    twin_of_delta = management.effective_params(
        {"book": "advised:early-delta", "advice_params": "{}"},
        {"defaults": management.PARAM_DEFAULTS, "books": {"delta": {}}, "advice": {"base_book": "delta"}},
    )
    decision, _ = management.evaluate(
        _position(book="advised:early-delta"), twin_of_delta, trigger_state={}, tick=tick, addon_credit=None
    )
    assert decision.action != "hold" or decision.reason != "not_triggered"
    twin_of_control = management.effective_params(
        {"book": "advised:early-delta", "advice_params": "{}"},
        {"defaults": management.PARAM_DEFAULTS, "books": {"control": {}}, "advice": {"base_book": "control"}},
    )
    decision, _ = management.evaluate(
        _position(book="advised:early-delta"), twin_of_control, trigger_state={}, tick=tick, addon_credit=None
    )
    assert decision.action == "hold" and decision.reason == "not_triggered"
