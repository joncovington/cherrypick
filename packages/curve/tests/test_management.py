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
