from datetime import date

import pytest
from test_evaluate import SESSION, leg, run

from cherrypick.manage import rollcheck as rc
from cherrypick.manage import rules as _rules

RULES = _rules.effective(None)[0]


def check(trade, closes, opens, spot, **kw):
    return rc.check(
        trade,
        [rc.parse_leg(x) for x in closes],
        [rc.parse_leg(x) for x in opens],
        session=SESSION,
        spot=spot,
        rules=RULES,
        **kw,
    )


def test_leg_text_is_read_and_bad_text_refused():
    assert rc.parse_leg("-2 2026-11-20 415C @ 2.57") == {
        "quantity": -2.0,
        "expiration": "2026-11-20",
        "strike": 415.0,
        "right": "C",
        "price": 2.57,
    }
    with pytest.raises(ValueError):
        rc.parse_leg("sell two 415 calls")


def test_glds_october_roll_is_flagged_as_risky():
    # GLD before the 10/09 roll: a Nov 410C long against Oct 23 425/445 short calls.
    before = run(
        [
            leg(1, "C", 410, "2026-11-20", 15.17, 6.00),
            leg(-2, "C", 425, "2026-10-23", 4.27, 0.14),
            leg(1, "C", 445, "2026-10-23", 1.80, 0.05),
        ],
        spot=395,
    )
    out = check(
        before,
        ["-2 2026-10-23 425C @0.14", "+1 2026-10-23 445C @0.05"],
        ["-2 2026-11-20 415C @2.57", "+1 2026-11-20 435C @1.01"],
        spot=395,
        net_liq=15889,
    )
    assert out["credit"] == pytest.approx(390)
    assert out["verdict"] == "risky"
    assert (out["shape_before"], out["shape_after"]) == ("diagonal", "broken-wing butterfly")
    text = " ".join(out["problems"])
    assert "shapes changes" not in text and "the shape changes from diagonal to broken-wing butterfly" in text
    assert "move into the long's own expiry" in text
    assert "what the trade can lose grows" in text
    assert out["max_loss_after"] > out["max_loss_before"]


def test_a_debit_roll_is_rejected():
    t = run([leg(-1, "P", 16, "2026-11-20", 1.01, 1.445)], spot=15.225)
    out = check(t, ["-1 2026-11-20 16P @1.50"], ["-1 2026-12-18 14P @0.80"], spot=15.225)
    assert out["credit"] < 0
    assert out["verdict"] == "reject"


def test_a_naked_put_rolled_through_earnings_is_rejected():
    t = run([leg(-1, "P", 16, "2026-11-20", 1.01, 1.445)], spot=15.225)
    out = check(
        t, ["-1 2026-11-20 16P @1.45"], ["-1 2026-12-18 16P @1.80"], spot=15.225, earnings_date="2026-10-27"
    )
    assert out["credit"] == pytest.approx(35)
    assert out["verdict"] == "reject"
    assert any("through earnings" in p for p in out["problems"])


def test_a_same_strike_roll_out_in_time_for_a_credit_is_ok():
    t = run([leg(-1, "P", 16, "2026-11-20", 1.01, 1.445)], spot=15.225)
    out = check(t, ["-1 2026-11-20 16P @1.45"], ["-1 2026-12-18 16P @1.80"], spot=15.225)
    assert out["verdict"] == "ok", out["problems"]
    assert out["shape_after"] == "naked put"


def test_closing_a_leg_the_trade_does_not_hold_is_an_error():
    t = run([leg(-1, "P", 16, "2026-11-20", 1.01, 1.445)], spot=15.225)
    with pytest.raises(ValueError):
        check(t, ["-1 2026-11-20 17P @1.45"], [], spot=15.225)


def test_session_constant_is_the_one_the_trades_use():
    assert SESSION == date(2026, 10, 13)
