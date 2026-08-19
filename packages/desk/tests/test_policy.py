"""The gate layer — the half of the desk's security that binds no matter who is asking.

The properties worth pinning are the fail-closed ones: an empty config refuses, an unreachable
broker refuses, an unbounded worst case cannot satisfy a finite cap, and the halt flag overrides
everything. Each is a place where a plausible implementation would have fallen *open*.
"""

import pytest

from cherrypick.desk import config as cfgmod
from cherrypick.desk import policy
from cherrypick.desk.order import analyze

pytestmark = pytest.mark.unit

ACCOUNT = "5WI62375"


def _cfg(**over):
    base = {
        "enabled": True,
        "allowed_accounts": ["2375"],
        "require_defined_risk": True,
        "max_order_risk_dollars": 500.0,
    }
    base.update(over)
    return cfgmod.resolve(base)


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
CLOSING = [
    _leg("BKNG  260807P00180000", "buy to close"),
    _leg("BKNG  260807C00210000", "buy to close"),
    _leg("BKNG  260807P00175000", "sell to close"),
    _leg("BKNG  260807C00215000", "sell to close"),
]


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
    """The single most important property: a missing or empty desk.json must not read as permissive.
    `resolve({})` lands on disabled + no allowed accounts, so both gates fire."""
    refusals = _evaluate(_risk(FLY, 1.10, "debit"), cfgmod.resolve({}))
    assert any("enabled is false" in r for r in refusals)
    assert any("allowed_accounts is empty" in r for r in refusals)


def test_disabled_desk_refuses():
    assert any("enabled is false" in r for r in _evaluate(_risk(FLY, 1.10, "debit"), _cfg(enabled=False)))


def test_unresolved_account_refuses_rather_than_passing():
    """An unreachable broker yields no account number. That must refuse — the alternative is
    skipping the allowlist entirely whenever the network is down."""
    refusals = _evaluate(_risk(FLY, 1.10, "debit"), account=None)
    assert any("no account resolved" in r for r in refusals)


# --------------------------------------------------------------------------- the halt flag
def test_halt_flag_refuses_even_a_perfect_order():
    refusals = _evaluate(_risk(FLY, 1.10, "debit"), halt=True)
    assert any("halt flag" in r for r in refusals)


def test_halt_flag_also_blocks_closing_orders():
    """The halt flag is a full stop, not a 'no new risk' brake — when it is set, the desk does
    nothing at all and the human goes to the broker UI."""
    refusals = _evaluate(_risk(CLOSING, 1.47, "debit"), halt=True)
    assert any("halt flag" in r for r in refusals)


# --------------------------------------------------------------------------- the allowlist
def test_account_outside_the_allowlist_refuses():
    refusals = _evaluate(_risk(FLY, 1.10, "debit"), account="9WI99999")
    assert any("not in desk.allowed_accounts" in r for r in refusals)


def test_allowlist_matches_on_last_four_only():
    """Config holds last-4 fragments, never full account numbers (suite-wide masking rule)."""
    assert _evaluate(_risk(FLY, 1.10, "debit"), _cfg(allowed_accounts=["5WI62375"])) == []


def test_refusal_text_never_leaks_a_full_account_number():
    refusals = _evaluate(_risk(FLY, 1.10, "debit"), account="9WI99999")
    assert not any("9WI99999" in r for r in refusals)
    assert any("****9999" in r for r in refusals)


# --------------------------------------------------------------------------- risk gates
def test_undefined_risk_refused_when_required():
    refusals = _evaluate(_risk(NAKED, 1.44, "credit"))
    assert any("undefined risk" in r for r in refusals)


def test_undefined_risk_allowed_when_configured_off_but_still_capped():
    """require_defined_risk is configurable, but turning it off must not also silently disable the
    dollar cap — an unbounded worst case can never satisfy a finite one, and says so explicitly."""
    refusals = _evaluate(_risk(NAKED, 1.44, "credit"), _cfg(require_defined_risk=False))
    assert not any("require_defined_risk" in r for r in refusals)  # that gate is off
    assert any("cannot satisfy" in r for r in refusals)  # the cap still catches it


def test_undefined_risk_with_no_cap_at_all_is_the_only_way_through():
    """Both brakes off is a deliberate, explicit configuration — not something reachable by
    forgetting a key (absent keys default to on/500)."""
    cfg = _cfg(require_defined_risk=False, max_order_risk_dollars=None)
    assert _evaluate(_risk(NAKED, 1.44, "credit"), cfg) == []


def test_worst_case_over_the_cap_refuses():
    """The BKNG condor risks 350 — fine at the 500 default, refused at 200."""
    assert _evaluate(_risk(FLY, 1.10, "debit"), _cfg(max_order_risk_dollars=50)) != []
    refusals = _evaluate(_risk(FLY, 1.10, "debit"), _cfg(max_order_risk_dollars=50))
    assert any("exceeds desk.max_order_risk_dollars" in r for r in refusals)


def test_default_cap_is_five_hundred():
    assert cfgmod.resolve({})["max_order_risk_dollars"] == 500.0


# --------------------------------------------------------------------------- closing orders
def test_closing_orders_skip_the_risk_cap():
    """A close removes exposure. Blocking it on a risk cap is the cap misfiring — exactly what an
    account-level deploy governor did to a BKNG close, which is why this package exists."""
    cfg = _cfg(max_order_risk_dollars=1.0)  # absurdly tight; a close must still pass
    assert _evaluate(_risk(CLOSING, 1.47, "debit"), cfg) == []


def test_closing_a_naked_short_is_allowed_even_with_defined_risk_required():
    """Flattening a naked short is precisely the thing you never want to refuse."""
    legs = [_leg("XYZ   260807C00091000", "buy to close")]
    assert _evaluate(_risk(legs, 1.44, "debit"), _cfg(require_defined_risk=True)) == []


def test_a_roll_is_held_to_the_opening_bar():
    """Mixed open/close establishes new legs, so it clears the same gates as an opening order.

    This particular roll is also a calendar (two expirations), so what stops it is the
    not-computable gate rather than an unbounded tail — and the message must say which."""
    legs = [
        _leg("XYZ   260807C00091000", "buy to close"),
        _leg("XYZ   260814C00091000", "sell to open"),
    ]
    refusals = _evaluate(_risk(legs, 0.30, "credit"), _cfg(require_defined_risk=True))
    assert any("multiple expirations" in r for r in refusals)


def test_a_same_expiry_roll_is_scored_normally():
    """Not every mixed order is a calendar — rolling strikes within one expiry stays computable."""
    legs = [
        _leg("XYZ   260807C00091000", "buy to close"),
        _leg("XYZ   260807C00097000", "sell to open"),
        _leg("XYZ   260807C00101000", "buy to open"),
    ]
    refusals = _evaluate(_risk(legs, 0.30, "credit"), _cfg(require_defined_risk=True))
    assert not any("multiple expirations" in r for r in refusals)


# --------------------------------------------------------------------------- daily brakes
def test_daily_order_cap_when_configured():
    cfg = _cfg(max_orders_per_day=2)
    assert _evaluate(_risk(FLY, 1.10, "debit"), cfg, orders_today=1) == []
    assert any("daily order cap" in r for r in _evaluate(_risk(FLY, 1.10, "debit"), cfg, orders_today=2))


def test_daily_risk_cap_counts_the_pending_order():
    cfg = _cfg(max_daily_risk_dollars=200)
    refusals = _evaluate(_risk(FLY, 1.10, "debit"), cfg, risk_today=150.0)
    assert any("desk.max_daily_risk_dollars" in r for r in refusals)


def test_daily_brakes_are_off_unless_configured():
    """Off by default so they never surprise — the user opted into the cap and the halt flag, not
    these."""
    resolved = cfgmod.resolve({})
    assert resolved["max_orders_per_day"] is None and resolved["max_daily_risk_dollars"] is None
    assert _evaluate(_risk(FLY, 1.10, "debit"), _cfg(), orders_today=999, risk_today=1e9) == []


# --------------------------------------------------------------------------- reporting shape
def test_every_refusal_is_reported_not_just_the_first():
    """A human fixing a proposal should see everything wrong at once rather than peeling them off
    one round-trip at a time."""
    cfg = cfgmod.resolve({"enabled": False, "allowed_accounts": [], "max_order_risk_dollars": 1})
    refusals = _evaluate(_risk(NAKED, 1.44, "credit"), cfg, halt=True)
    assert len(refusals) >= 4


# --------------------------------------------------------------------------- evaluate_management
def test_management_is_exempt_from_the_halt_flag():
    """Pulling a resting order only reduces exposure — the same reason closing orders skip the risk
    gates in `evaluate`. A halt that trapped an account inside a stale order would be the safety flag
    misfiring in the direction that increases risk, not the one it exists to guard against.

    (`evaluate_management` has no `halt_present` parameter at all — this test would fail to compile a
    call with one, which is itself part of the assertion: the halt flag was never plumbed in.)
    """
    cfg = _cfg()
    assert policy.evaluate_management(cfg=cfg, account_number=ACCOUNT) == []


def test_management_still_checks_desk_enabled():
    cfg = _cfg(enabled=False)
    refusals = policy.evaluate_management(cfg=cfg, account_number=ACCOUNT)
    assert any("desk.enabled" in r for r in refusals)


def test_management_still_checks_the_account_allowlist():
    cfg = _cfg(allowed_accounts=["9999"])
    refusals = policy.evaluate_management(cfg=cfg, account_number=ACCOUNT)
    assert any("allowed_accounts" in r for r in refusals)


def test_management_refuses_an_unresolved_account():
    cfg = _cfg()
    refusals = policy.evaluate_management(cfg=cfg, account_number=None)
    assert any("no account resolved" in r for r in refusals)
