"""Per-book verdicts, the execution gate, the exposure flag, and the advised overlay."""

from datetime import datetime

from cherrypick.pmcc import management

NOW = datetime(2026, 8, 24, 11, 0)

POSITION = {
    "position_id": "TQQQ:control:2026-08-17",
    "book": "control",
    "short_strike": 67.0,
    "short_expiration": "2026-08-28",
    "long_expiration": "2026-09-04",
}


def _params(book="control", **over):
    p = {**management.PARAM_DEFAULTS, "book": book}
    p.update(over)
    return p


def test_holds_before_short_expiration_regardless_of_tv():
    # The default control exit no longer reacts to tv at all -- it holds until the short's own
    # expiration day, whatever the time value is doing.
    d = management.evaluate(POSITION, _params(), now=NOW, short_tv=0.02, spot=70.0)
    assert d.action == "hold"
    assert d.reason == "holding_to_expiry"


def test_closes_both_legs_on_short_expiration_day():
    d = management.evaluate(POSITION, _params(), now=datetime(2026, 8, 28, 10, 0), short_tv=0.50, spot=70.0)
    assert d.action == "close_all"
    assert d.reason == "short_expiration"


def test_tv_managed_exit_override_closes_early():
    d = management.evaluate(POSITION, _params(tv_managed_exit=True), now=NOW, short_tv=0.08, spot=70.0)
    assert d.action == "close_all"
    assert d.reason == "tv_exhausted"


def test_tv_managed_exit_override_holds_while_tv_remains():
    d = management.evaluate(POSITION, _params(tv_managed_exit=True), now=NOW, short_tv=0.80, spot=70.0)
    assert d.action == "hold"
    assert d.reason == "working"


def test_unpriced_mark_never_acts():
    d = management.evaluate(POSITION, _params(), now=NOW, short_tv=None, spot=70.0)
    assert d.action == "hold"
    assert d.reason == "unpriced_mark"


def test_assignment_exposed_flag():
    assert management.assignment_exposed(0.03, _params())
    assert not management.assignment_exposed(0.08, _params())
    assert not management.assignment_exposed(None, _params())


def test_assignment_exposed_exempts_cash_settlement():
    # A thin extrinsic on a European, cash-settled short (XSP) is not an early-assignment risk --
    # it can only be exercised at its own expiration -- so the telemetry must never fire for it,
    # however deep the extrinsic sits under the threshold.
    assert not management.assignment_exposed(0.01, _params(), settlement_style="cash")
    assert not management.assignment_exposed(0.0, _params(), settlement_style="cash")
    # Physical (TQQQ) and "unknown" (legacy call sites without a style) keep the old behavior.
    assert management.assignment_exposed(0.03, _params(), settlement_style="physical")
    assert management.assignment_exposed(0.03, _params())


def test_execution_gate():
    ok_mark = {"ok": True, "max_spread_pct": 0.05}
    assert management.execution_gate(ok_mark, _params(), now=NOW) is None
    assert management.execution_gate({"ok": False}, _params(), now=NOW) == "unusable_mark"
    early = datetime(2026, 8, 24, 9, 35)
    assert management.execution_gate(ok_mark, _params(), now=early) == "before_exec_window"
    wide = {"ok": True, "max_spread_pct": 0.40}
    assert management.execution_gate(wide, _params(), now=NOW) == "spread_too_wide"


def test_effective_params_advised_overlay_and_control_untouched(config):
    advised = {**POSITION, "book": "advised:control", "advice_params": '{"tv_managed_exit": true}'}
    params = management.effective_params(advised, config)
    assert params["tv_managed_exit"] is True
    assert params["book"] == "advised:control"
    control = management.effective_params(POSITION, config)
    assert control["tv_managed_exit"] is False
    # The advised row still resolves through the base book's control rules -- the tv-exit override
    # is a param, not a book fork.
    d = management.evaluate(advised, params, now=NOW, short_tv=0.08, spot=70.0)
    assert d.action == "close_all"
    assert d.reason == "tv_exhausted"


def test_a_penny_wide_leg_is_not_too_wide_to_close():
    """This module holds the short to its own expiration by design, which is exactly when its quote goes penny-wide: 0.00/0.01 is a one-cent buyback and a 200% ratio, and refusing it blocks the combined disposal on the day the design says to take it. Verified by restoring the aggregate percentage test and watching this admit-case
    refuse."""
    snap = {
        "ok": True,
        "max_spread_pct": 2.0,
        "leg_spreads": [
            {"symbol": "short", "pct": 2.0, "abs": 0.01},
            {"symbol": "long", "pct": 0.05, "abs": 0.02},
        ],
    }
    assert management.execution_gate(snap, _params(), now=NOW) is None


def test_a_leg_wide_in_money_as_well_as_percent_still_blocks():
    snap = {"ok": True, "max_spread_pct": 2.0, "leg_spreads": [{"symbol": "s", "pct": 2.0, "abs": 0.60}]}
    assert management.execution_gate(snap, _params(), now=NOW) == "spread_too_wide"


def test_the_two_readings_are_judged_per_leg_not_as_separate_maxima():
    snap = {
        "ok": True,
        "max_spread_pct": 2.0,
        "leg_spreads": [
            {"symbol": "cheap-wide-pct", "pct": 2.0, "abs": 0.01},
            {"symbol": "fat-wide-money", "pct": 0.05, "abs": 0.60},
        ],
    }
    assert management.execution_gate(snap, _params(), now=NOW) is None
