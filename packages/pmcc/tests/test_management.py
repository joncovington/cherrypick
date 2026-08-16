"""Per-book verdicts, the execution gate, the exposure flag, and the advised overlay."""

from datetime import datetime

from cherrypick.pmcc import management

NOW = datetime(2026, 8, 24, 11, 0)

POSITION = {
    "position_id": "TNA:control:2026-08-17",
    "book": "control",
    "short_strike": 67.0,
    "long_expiration": "2026-09-04",
}


def _params(book="control", **over):
    p = {**management.PARAM_DEFAULTS, "book": book}
    p.update(over)
    return p


def test_tv_exhausted_closes_both_legs():
    d = management.evaluate(POSITION, _params(), now=NOW, short_tv=0.08, spot=70.0)
    assert d.action == "close_all"
    assert d.reason == "tv_exhausted"


def test_working_hold_while_tv_remains():
    d = management.evaluate(POSITION, _params(), now=NOW, short_tv=0.80, spot=70.0)
    assert d.action == "hold"
    assert d.reason == "working"


def test_breach_holds_like_covered_call_for_control_and_keltner():
    for book in ("control", "keltner"):
        d = management.evaluate({**POSITION, "book": book}, _params(book), now=NOW, short_tv=1.20, spot=66.0)
        assert d.action == "hold"
        assert d.reason == "covered_call_hold"


def test_breach_low_tv_still_holds_not_closes():
    # Spot below the strike with tiny TV is the covered-call ride, NOT a tv_exhausted close.
    d = management.evaluate(POSITION, _params(), now=NOW, short_tv=0.05, spot=66.0)
    assert d.action == "hold"


def test_roll_book_rolls_on_breach():
    d = management.evaluate({**POSITION, "book": "roll"}, _params("roll"), now=NOW, short_tv=1.20, spot=66.0)
    assert d.action == "roll_short"
    assert d.reason == "short_strike_breach"


def test_roll_book_once_per_session():
    d = management.evaluate(
        {**POSITION, "book": "roll"}, _params("roll"), now=NOW, short_tv=1.20, spot=66.0, rolled_today=True
    )
    assert d.action == "hold"
    assert d.reason == "roll_cadence"


def test_roll_exhausted_when_long_is_short_dated():
    d = management.evaluate(
        {**POSITION, "book": "roll", "long_expiration": "2026-08-26"},
        _params("roll"),
        now=NOW,
        short_tv=1.20,
        spot=66.0,
    )
    assert d.action == "close_all"
    assert d.reason == "roll_exhausted"


def test_unpriced_mark_never_acts():
    d = management.evaluate(POSITION, _params(), now=NOW, short_tv=None, spot=70.0)
    assert d.action == "hold"
    assert d.reason == "unpriced_mark"


def test_assignment_exposed_flag():
    assert management.assignment_exposed(0.03, _params())
    assert not management.assignment_exposed(0.08, _params())
    assert not management.assignment_exposed(None, _params())


def test_execution_gate():
    ok_mark = {"ok": True, "max_spread_pct": 0.05}
    assert management.execution_gate(ok_mark, _params(), now=NOW) is None
    assert management.execution_gate({"ok": False}, _params(), now=NOW) == "unusable_mark"
    early = datetime(2026, 8, 24, 9, 35)
    assert management.execution_gate(ok_mark, _params(), now=early) == "before_exec_window"
    wide = {"ok": True, "max_spread_pct": 0.40}
    assert management.execution_gate(wide, _params(), now=NOW) == "spread_too_wide"


def test_effective_params_advised_overlay_and_control_untouched(config):
    advised = {**POSITION, "book": "advised:control", "advice_params": '{"tv_close_threshold": 0.2}'}
    params = management.effective_params(advised, config)
    assert params["tv_close_threshold"] == 0.2
    assert params["book"] == "advised:control"
    control = management.effective_params(POSITION, config)
    assert control["tv_close_threshold"] == 0.10
    # The advised base's book rules resolve through the base: an advised:roll row still rolls.
    advised_roll = {**POSITION, "book": "advised:roll", "advice_params": None}
    d = management.evaluate(
        advised_roll, management.effective_params(advised_roll, config), now=NOW, short_tv=1.2, spot=66.0
    )
    assert d.action == "roll_short"
