from datetime import datetime

from cherrypick.curve import engine, management

PARAMS = {**management.PARAM_DEFAULTS, "book": "control"}


def _position(**overrides):
    base = {
        "book": "control",
        "expiration": "2026-10-16",
        "entry_credit": 1.00,
        "advice_params": None,
    }
    base.update(overrides)
    return base


def test_profit_take_fires_at_target():
    position = _position()
    now = datetime(2026, 9, 1)
    decision = management.evaluate(
        position, PARAMS, now=now, close_cost=0.49, regime={"ok": True, "regime": "contango"}
    )
    assert decision.action == "close_all"
    assert decision.reason == "profit_take"


def test_holds_above_profit_take_target():
    position = _position()
    now = datetime(2026, 9, 1)
    decision = management.evaluate(
        position, PARAMS, now=now, close_cost=0.60, regime={"ok": True, "regime": "contango"}
    )
    assert decision.action == "hold"
    assert decision.reason == "working"


def test_close_dte_overrides_everything():
    position = _position(expiration="2026-09-05")
    now = datetime(2026, 8, 30)  # 6 dte, under close_dte=7
    decision = management.evaluate(position, PARAMS, now=now, close_cost=0.99, regime=None)
    assert decision.action == "close_all"
    assert decision.reason == "close_dte"


def test_flip_exit_fires_on_control_when_measured_backwardation():
    position = _position()
    now = datetime(2026, 9, 1)
    decision = management.evaluate(
        position,
        PARAMS,
        now=now,
        close_cost=0.99,
        regime={"ok": True, "regime": "backwardation", "ratio": 1.05},
    )
    assert decision.action == "close_all"
    assert decision.reason == "regime_flip"


def test_flip_exit_never_fires_on_noflip():
    noflip_params = {**management.PARAM_DEFAULTS, "book": "noflip"}
    position = _position(book="noflip")
    now = datetime(2026, 9, 1)
    decision = management.evaluate(
        position,
        noflip_params,
        now=now,
        close_cost=0.99,
        regime={"ok": True, "regime": "backwardation", "ratio": 1.05},
    )
    assert decision.action == "hold"
    assert decision.reason == "working"


def test_flip_exit_never_fires_on_unmeasured_regime():
    """Rule 6: missing regime data can never force an exit — only a MEASURED crossing flips."""
    position = _position()
    now = datetime(2026, 9, 1)
    decision = management.evaluate(
        position, PARAMS, now=now, close_cost=0.99, regime={"ok": False, "reason": "stale_vix"}
    )
    assert decision.action == "hold"


def test_flip_exit_never_fires_on_absent_regime_row():
    position = _position()
    now = datetime(2026, 9, 1)
    decision = management.evaluate(position, PARAMS, now=now, close_cost=0.99, regime=None)
    assert decision.action == "hold"


def test_unpriced_mark_holds():
    position = _position()
    now = datetime(2026, 9, 1)
    decision = management.evaluate(
        position, PARAMS, now=now, close_cost=None, regime={"ok": True, "regime": "contango"}
    )
    assert decision.action == "hold"
    assert decision.reason == "unpriced_mark"


def test_effective_params_untouched_for_control():
    position = _position()
    config = {"defaults": {"profit_take_pct": 0.55}, "books": {"control": {}}}
    params = management.effective_params(position, config)
    assert params["profit_take_pct"] == 0.55
    assert params["book"] == "control"


def test_effective_params_overlays_advised():
    position = {"book": "advised:control", "advice_params": '{"profit_take_pct": 0.35}'}
    config = {"defaults": {"profit_take_pct": 0.50}, "books": {"control": {}}}
    params = management.effective_params(position, config)
    assert params["profit_take_pct"] == 0.35


def test_assignment_exposed_flag():
    params = {"assignment_exposure_tv": 0.05}
    assert management.assignment_exposed(0.02, params) is True
    assert management.assignment_exposed(0.10, params) is False
    assert management.assignment_exposed(None, params) is False


# --------------------------------------------------------------------------- control/noflip pairing
def test_control_and_noflip_share_the_same_plan_from_one_snapshot():
    """The exact-pairing property: control and noflip enter from the SAME plan on the same tick —
    identical strikes, mids, and modeled costs."""
    chain = [
        {
            "strike_price": 30,
            "streamer_symbol": "s30",
            "occ_symbol": "VXX   260918C00030000",
            "option_type": "call",
        },
        {
            "strike_price": 35,
            "streamer_symbol": "s35",
            "occ_symbol": "VXX   260918C00035000",
            "option_type": "call",
        },
    ]
    quotes = {"s30": {"bid": 0.9, "ask": 1.1, "mid": 1.0}, "s35": {"bid": 0.35, "ask": 0.45, "mid": 0.40}}
    greeks = {"s30": {"delta": 0.30}, "s35": {"delta": 0.12}}
    snapshot = {
        "symbol": "VXX",
        "spot": 18.0,
        "expiration": "2026-09-18",
        "dte": 37,
        "chain": chain,
        "quotes": quotes,
        "greeks": greeks,
    }
    params = {
        "short_delta_target": 0.30,
        "spread_width": 5.0,
        "min_credit_pct_of_width": 0.05,
        "max_leg_spread_pct": 0.25,
    }
    control_plan = engine.plan_entry(snapshot, params)
    noflip_plan = engine.plan_entry(snapshot, params)
    assert control_plan == noflip_plan


# --------------------------------------------------------------- the spread gate reads money too
NOON = datetime(2026, 9, 1, 12, 0)


def test_a_penny_wide_leg_is_not_too_wide_to_close():
    """The far-OTM VXX wing is routinely bid-less (this module's own entry finding: 56 of 62
    refusals in one session at exactly 2.000), so at any exit the wing alone would gate the whole
    close on a percentage test. Verified by restoring the aggregate percentage and watching this
    admit-case refuse."""
    snap = {
        "ok": True,
        "max_spread_pct": 2.0,
        "leg_spreads": [
            {"symbol": "wing", "pct": 2.0, "abs": 0.01},
            {"symbol": "short", "pct": 0.08, "abs": 0.03},
        ],
    }
    assert management.execution_gate(snap, dict(management.PARAM_DEFAULTS), now=NOON) is None


def test_a_leg_wide_in_money_as_well_as_percent_still_blocks():
    snap = {"ok": True, "max_spread_pct": 2.0, "leg_spreads": [{"symbol": "s", "pct": 2.0, "abs": 0.60}]}
    assert management.execution_gate(snap, dict(management.PARAM_DEFAULTS), now=NOON) == "spread_too_wide"


def test_a_snapshot_without_leg_detail_keeps_the_percentage_test():
    assert (
        management.execution_gate(
            {"ok": True, "max_spread_pct": 2.0}, dict(management.PARAM_DEFAULTS), now=NOON
        )
        == "spread_too_wide"
    )


def test_frozen_params_govern_an_open_advised_position_after_the_artifact_expires():
    """The exit-continuity rule, pinned (2026-09-12): an expired artifact admits no new entry,
    but the params frozen on an open advised row govern its exits until it closes."""
    import json

    from cherrypick.core import advice as core_advice

    bounds = {"profit_take_pct": {"min": 0.2, "max": 0.9}}
    expired = {
        "module": "curve",
        "session": "2026-09-11",
        "expires_at": "2026-09-11T23:59:59-04:00",
        "proposals": [{"param": "profit_take_pct", "value": 0.35, "rationale": "r"}],
    }
    assert core_advice.validate(expired, bounds, "2026-09-11")["ok"] is False

    position = {"book": "advised:control", "advice_params": json.dumps({"profit_take_pct": 0.35})}
    config = {"defaults": {"profit_take_pct": 0.50}, "books": {"control": {}}}
    assert management.effective_params(position, config)["profit_take_pct"] == 0.35


# --------------------------- one advised book per experiment (2026-09-17)


def test_base_book_resolves_an_experiment_tag_through_the_decision_then_the_config():
    """Since 2026-09-17 an advised tag names the EXPERIMENT, not the base. The decision entry
    answers first, the configured `advice.base_book` answers for a row whose decision is gone, and
    the legacy `advised:<base>` tag still reads its base straight off the name."""
    decision = {
        "experiments": [
            {"experiment_id": "exp-1", "tag": "advised:hook-take", "base": "hook", "params": {"a": 1}}
        ]
    }
    assert engine.base_book("advised:hook-take", decision=decision) == "hook"
    assert engine.base_book("advised:hook-take") == "control"
    assert engine.base_book("advised:hook-take", config={"advice": {"base_book": "noflip"}}) == "noflip"
    assert engine.base_book("advised:hook", config={"advice": {"base_book": "noflip"}}) == "hook"
    assert engine.base_book("noflip") == "noflip"


def test_effective_params_resolve_an_experiment_tag_to_the_configured_base():
    config = {
        "defaults": {"profit_take_pct": 0.5},
        "books": {"control": {}, "noflip": {"profit_take_pct": 0.4}},
    }
    row = {"book": "advised:take-35", "advice_params": '{"profit_take_pct": 0.35}'}
    params = management.effective_params(row, config)
    assert params["base_book"] == "control" and params["profit_take_pct"] == 0.35
    config["advice"] = {"base_book": "noflip"}
    params = management.effective_params({"book": "advised:take-35", "advice_params": "{}"}, config)
    assert params["base_book"] == "noflip" and params["profit_take_pct"] == 0.4


def test_an_experiment_twin_of_noflip_never_flips_but_a_twin_of_control_does():
    """The flip exit keys on the resolved base, never on the tag: the same `advised:<name>` tag
    shadowing `noflip` holds through measured backwardation, shadowing `control` closes."""
    backwardation = {"ok": True, "regime": "backwardation", "ratio": 1.05}
    now = datetime(2026, 9, 1)
    of_noflip = management.effective_params(
        {"book": "advised:take-35", "advice_params": "{}"},
        {"defaults": {}, "books": {"noflip": {}}, "advice": {"base_book": "noflip"}},
    )
    d = management.evaluate(
        _position(book="advised:take-35"), of_noflip, now=now, close_cost=0.99, regime=backwardation
    )
    assert d.action == "hold" and d.reason == "working"
    of_control = management.effective_params(
        {"book": "advised:take-35", "advice_params": "{}"},
        {"defaults": {}, "books": {"control": {}}, "advice": {"base_book": "control"}},
    )
    d = management.evaluate(
        _position(book="advised:take-35"), of_control, now=now, close_cost=0.99, regime=backwardation
    )
    assert d.action == "close_all" and d.reason == "regime_flip"
