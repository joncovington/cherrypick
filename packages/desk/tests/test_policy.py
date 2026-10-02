"""The gate layer — the half of the desk's security that binds no matter who is asking.

The properties worth pinning are the fail-closed ones: an empty config refuses, an unreachable
broker refuses, an unbounded worst case cannot satisfy a finite cap, a cap that is missing or junk
refuses rather than reading as "no cap", and the halt flag overrides everything. Each is a place
where a plausible implementation would have fallen *open*.
"""

import math

import pytest

from cherrypick.desk import config as cfgmod
from cherrypick.desk import policy
from cherrypick.desk.order import analyze

pytestmark = pytest.mark.unit

ACCOUNT = "5WT01234"


def _cfg(**over):
    base = {
        "enabled": True,
        "allowed_accounts": ["1234"],
        "allowed_underlyings": ["XYZ", "BKNG"],
        "require_defined_risk": True,
        "max_order_risk_dollars": 500.0,
        "max_daily_risk_dollars": 5000.0,
        "max_orders_per_day": 10,
    }
    base.update(over)
    cfg = cfgmod.resolve(base)
    assert cfg["config_errors"] == [], cfg["config_errors"]
    return cfg


def _risk(legs, price, effect):
    _, risk = analyze({"legs": legs, "price": price, "price_effect": effect})
    return risk


def _leg(symbol, action, qty=1):
    return {"instrument_type": "Equity Option", "symbol": symbol, "action": action, "quantity": qty}


FLY = [
    _leg("XYZ   260807C00085000", "buy to open"),
    _leg("XYZ   260807C00091000", "sell to open", 2),
    _leg("XYZ   260807C00097000", "buy to open"),
]
NAKED = [_leg("XYZ   260807C00091000", "sell to open")]
CONDOR_CLOSE = [
    _leg("BKNG  260807P00180000", "buy to close"),
    _leg("BKNG  260807C00210000", "buy to close"),
    _leg("BKNG  260807P00175000", "sell to close"),
    _leg("BKNG  260807C00215000", "sell to close"),
]
COVER = [
    _leg("BKNG  260807P00180000", "buy to close"),
    _leg("BKNG  260807C00210000", "buy to close"),
]
SELL_LONG_WING = [_leg("BKNG  260807C00215000", "sell to close")]


def _evaluate(risk, cfg=None, *, halt=False, account=ACCOUNT, orders_today=0, risk_today=0.0):
    return policy.evaluate(
        risk,
        cfg=cfg or _cfg(),
        halt_present=halt,
        account_number=account,
        orders_today=orders_today,
        risk_today=risk_today,
    )


# --------------------------------------------------------------------------- the happy path
def test_a_compliant_defined_risk_order_passes():
    assert _evaluate(_risk(FLY, 1.10, "debit")) == []


# --------------------------------------------------------------------------- fail-closed defaults
def test_an_empty_config_refuses_everything():
    refusals = _evaluate(_risk(FLY, 1.10, "debit"), cfgmod.resolve({}))
    assert any("enabled is false" in r for r in refusals)
    assert any("allowed_accounts is empty" in r for r in refusals)
    assert any("allowed_underlyings is empty" in r for r in refusals)


def test_default_caps_refuse_the_110_dollar_fly():
    """The new defaults are deliberately small: a $110 worst case is over the $100 cap."""
    cfg = cfgmod.resolve({"enabled": True, "allowed_accounts": ["1234"], "allowed_underlyings": ["XYZ"]})
    refusals = _evaluate(_risk(FLY, 1.10, "debit"), cfg)
    assert any("exceeds desk.max_order_risk_dollars $100.00" in r for r in refusals)
    assert _evaluate(_risk(FLY, 0.90, "debit"), cfg) == []


def test_disabled_desk_refuses():
    assert any("enabled is false" in r for r in _evaluate(_risk(FLY, 1.10, "debit"), _cfg(enabled=False)))


def test_a_truthy_but_not_true_enabled_refuses_even_in_a_raw_dict():
    raw = {**_cfg(), "enabled": "yes"}
    assert any("enabled is false" in r for r in _evaluate(_risk(FLY, 1.10, "debit"), raw))


def test_config_errors_are_refusals():
    raw = {**_cfg(), "config_errors": ["max_order_risk_dollars must be a number"]}
    assert any("desk.json refused" in r for r in _evaluate(_risk(FLY, 1.10, "debit"), raw))


@pytest.mark.parametrize("value", [None, math.nan, math.inf, -5, "500", True])
@pytest.mark.parametrize(
    "key", ["max_order_risk_dollars", "max_spreads_per_order", "max_orders_per_day", "max_daily_risk_dollars"]
)
def test_a_junk_cap_in_a_raw_dict_refuses_rather_than_disabling(key, value):
    """Even bypassing `resolve`, a cap that is not a finite non-negative number refuses."""
    raw = {**_cfg(), key: value}
    refusals = _evaluate(_risk(FLY, 1.10, "debit"), raw)
    assert any(f"desk.{key} is not a finite non-negative number" in r for r in refusals)


def test_unresolved_account_refuses_rather_than_passing():
    refusals = _evaluate(_risk(FLY, 1.10, "debit"), account=None)
    assert any("no account resolved" in r for r in refusals)


# --------------------------------------------------------------------------- the halt flag
def test_halt_flag_refuses_even_a_perfect_order():
    assert any("halt flag" in r for r in _evaluate(_risk(FLY, 1.10, "debit"), halt=True))


def test_halt_flag_also_blocks_covering_orders():
    assert any("halt flag" in r for r in _evaluate(_risk(COVER, 1.47, "debit"), halt=True))


# --------------------------------------------------------------------------- allowlists
def test_account_outside_the_allowlist_refuses():
    refusals = _evaluate(_risk(FLY, 1.10, "debit"), account="9WI99999")
    assert any("not in desk.allowed_accounts" in r for r in refusals)


def test_allowlist_matches_on_last_four_only():
    assert _evaluate(_risk(FLY, 1.10, "debit"), _cfg(allowed_accounts=["5WT01234"])) == []


def test_refusal_text_never_leaks_a_full_account_number():
    refusals = _evaluate(_risk(FLY, 1.10, "debit"), account="9WI99999")
    assert not any("9WI99999" in r for r in refusals)
    assert any("****9999" in r for r in refusals)


def test_underlying_outside_the_allowlist_refuses():
    refusals = _evaluate(_risk(FLY, 1.10, "debit"), _cfg(allowed_underlyings=["SPY"]))
    assert any("XYZ is not in desk.allowed_underlyings" in r for r in refusals)


def test_empty_underlying_allowlist_refuses_even_a_cover():
    refusals = _evaluate(_risk(COVER, 1.47, "debit"), _cfg(allowed_underlyings=[]))
    assert any("allowed_underlyings is empty" in r for r in refusals)


# --------------------------------------------------------------------------- risk gates
def test_undefined_risk_refused_when_required():
    assert any("undefined risk" in r for r in _evaluate(_risk(NAKED, 1.44, "credit")))


def test_turning_defined_risk_off_still_leaves_the_cap():
    """There is no longer any way through for an unbounded order: the cap is always a number."""
    refusals = _evaluate(_risk(NAKED, 1.44, "credit"), _cfg(require_defined_risk=False))
    assert not any("require_defined_risk" in r for r in refusals)
    assert any("cannot satisfy" in r for r in refusals)


def test_worst_case_over_the_cap_refuses():
    refusals = _evaluate(_risk(FLY, 1.10, "debit"), _cfg(max_order_risk_dollars=50))
    assert any("exceeds desk.max_order_risk_dollars" in r for r in refusals)


def test_spread_count_over_the_cap_refuses():
    two_lot = [dict(leg, quantity=leg["quantity"] * 2) for leg in FLY]
    refusals = _evaluate(_risk(two_lot, 0.40, "debit"), _cfg(max_spreads_per_order=1))
    assert any("2 spreads exceeds desk.max_spreads_per_order 1" in r for r in refusals)
    assert _evaluate(_risk(two_lot, 0.40, "debit"), _cfg(max_spreads_per_order=2)) == []


# --------------------------------------------------------------------------- the covering exemption
def test_buy_to_close_only_skips_the_risk_gates():
    """Buying back shorts only removes exposure; a cap that blocks it is the cap misfiring."""
    cfg = _cfg(max_order_risk_dollars=1.0, max_spreads_per_order=0, max_orders_per_day=0)
    assert _evaluate(_risk(COVER, 1.47, "debit"), cfg, orders_today=99) == []


def test_closing_a_naked_short_is_allowed():
    legs = [_leg("XYZ   260807C00091000", "buy to close")]
    assert _evaluate(_risk(legs, 1.44, "debit")) == []


def test_selling_the_long_wing_of_a_condor_is_not_exempt():
    """The hole this closes: `sell to close` the long wing leaves a naked short. Seen alone, the
    order is a short call — unbounded — and must clear the opening bar."""
    refusals = _evaluate(_risk(SELL_LONG_WING, 0.20, "credit"))
    assert any("undefined risk" in r for r in refusals)


def test_closing_a_whole_condor_is_held_to_the_caps():
    """Mixed buy/sell-to-close is not a pure cover, so the caps apply. This one costs 147."""
    refusals = _evaluate(_risk(CONDOR_CLOSE, 1.47, "debit"), _cfg(max_order_risk_dollars=100))
    assert any("exceeds desk.max_order_risk_dollars" in r for r in refusals)


def test_a_roll_is_held_to_the_opening_bar():
    legs = [_leg("XYZ   260807C00091000", "buy to close"), _leg("XYZ   260814C00091000", "sell to open")]
    refusals = _evaluate(_risk(legs, 0.30, "credit"))
    assert any("multiple expirations" in r for r in refusals)


def test_a_same_expiry_roll_is_scored_normally():
    legs = [
        _leg("XYZ   260807C00091000", "buy to close"),
        _leg("XYZ   260807C00097000", "sell to open"),
        _leg("XYZ   260807C00101000", "buy to open"),
    ]
    refusals = _evaluate(_risk(legs, 0.30, "credit"))
    assert not any("multiple expirations" in r for r in refusals)


# --------------------------------------------------------------------------- daily brakes
def test_daily_order_cap():
    cfg = _cfg(max_orders_per_day=2)
    assert _evaluate(_risk(FLY, 1.10, "debit"), cfg, orders_today=1) == []
    assert any("daily order cap" in r for r in _evaluate(_risk(FLY, 1.10, "debit"), cfg, orders_today=2))


def test_daily_risk_cap_counts_the_pending_order():
    refusals = _evaluate(_risk(FLY, 1.10, "debit"), _cfg(max_daily_risk_dollars=200), risk_today=150.0)
    assert any("desk.max_daily_risk_dollars" in r for r in refusals)


def test_daily_brakes_are_on_by_default():
    resolved = cfgmod.resolve({})
    assert resolved["max_orders_per_day"] == 2 and resolved["max_daily_risk_dollars"] == 200.0


def test_every_refusal_is_reported_not_just_the_first():
    cfg = cfgmod.resolve({"enabled": False, "allowed_accounts": [], "max_order_risk_dollars": 1})
    assert len(_evaluate(_risk(NAKED, 1.44, "credit"), cfg, halt=True)) >= 4


# --------------------------------------------------------------------------- buying power
def _pf(change):
    return {"ok": True, "buying_power": {"change_in_buying_power": change}}


def test_buying_power_within_the_cap_passes():
    assert policy.evaluate_buying_power(_risk(FLY, 0.90, "debit"), cfg=_cfg(), preflight=_pf("-90.00")) == []


def test_buying_power_over_the_cap_refuses():
    refusals = policy.evaluate_buying_power(_risk(FLY, 0.90, "debit"), cfg=_cfg(), preflight=_pf("-250.00"))
    assert any("max_order_buying_power_dollars" in r for r in refusals)


@pytest.mark.parametrize(
    "preflight",
    [{"ok": True}, {"ok": True, "buying_power": {}}, _pf(None), _pf("None"), _pf("NaN"), _pf("abc"), None],
)
def test_a_preflight_without_a_buying_power_change_refuses(preflight):
    refusals = policy.evaluate_buying_power(_risk(FLY, 0.90, "debit"), cfg=_cfg(), preflight=preflight)
    assert any("no buying-power change" in r for r in refusals)


def test_a_cover_is_exempt_from_the_buying_power_cap():
    assert policy.evaluate_buying_power(_risk(COVER, 1.47, "debit"), cfg=_cfg(), preflight={"ok": True}) == []


# --------------------------------------------------------------------------- evaluate_management
def test_management_is_exempt_from_the_halt_flag():
    """`evaluate_management` has no `halt_present` parameter at all — the halt flag was never
    plumbed in, because pulling a resting order only reduces exposure."""
    assert policy.evaluate_management(cfg=_cfg(), account_number=ACCOUNT) == []


def test_management_still_checks_desk_enabled():
    refusals = policy.evaluate_management(cfg=_cfg(enabled=False), account_number=ACCOUNT)
    assert any("desk.enabled" in r for r in refusals)


def test_management_still_checks_the_account_allowlist():
    refusals = policy.evaluate_management(cfg=_cfg(allowed_accounts=["9993"]), account_number=ACCOUNT)
    assert any("allowed_accounts" in r for r in refusals)


def test_management_refuses_an_unresolved_account():
    refusals = policy.evaluate_management(cfg=_cfg(), account_number=None)
    assert any("no account resolved" in r for r in refusals)


def test_the_desk_masks_with_cores_rule():
    from cherrypick.core import redact

    assert policy.mask_account is redact.mask_account
