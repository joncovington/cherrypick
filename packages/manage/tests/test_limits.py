"""Your limits (limits.py): each measure beside the book. A limit you set wins; a blank one falls back
to the guidance band or the candidate cap, and says which; dollar delta and vega have none. States:
over, near (past `near_share` of the way), within. The day's loss crosses its alert, from each leg's
mark less its previous close. Synthetic figures."""

import pytest

from cherrypick.manage import limits
from cherrypick.manage import rules as _rules

BOOK = {
    "net_liq": 10_000.0,
    "bwd": -12.0,
    "benchmark_spot": 500.0,
    "theta_ex_front": 20.0,
    "theta": 25.0,
    "vega": -40.0,
    "buying_power_used_pct": 0.27,
    "bands": {
        "delta": {"low": -8.0, "high": 10.0},
        "theta": {"low": 0.001, "high": 0.003},
        "buying_power": {"low": 0.0, "high": 0.3},
    },
}
TRADES = [
    {"underlying": "AAA", "profile": {"max_loss_from_open": -800.0}},
    {"underlying": "AAA", "profile": {"max_loss_from_open": -400.0}},
    {"underlying": "BBB", "profile": {"max_loss_from_open": -500.0}},
]
LEGS = [
    {
        "underlying": "AAA",
        "legs": [
            {
                "symbol": "AAA 1",
                "right": "P",
                "quantity": -1,
                "mid": 1.0,
                "prev_close": 1.5,
                "multiplier": 100,
            },
            {
                "symbol": "AAA 2",
                "right": "P",
                "quantity": 1,
                "mid": 0.4,
                "prev_close": 0.5,
                "multiplier": 100,
            },
        ],
    }
]
IRA = {"margin_or_cash": "Cash", "type_name": "Traditional IRA"}


def read(overrides=None, book=BOOK, legs=LEGS):
    rules, warnings = _rules.effective({"limits": overrides} if overrides else None)
    assert warnings == []
    return limits.reading(book, TRADES, legs, IRA, rules)


def row(out, rid):
    return next(r for r in out["rows"] if r["id"] == rid)


def test_blank_limits_fall_back_to_the_bands_and_the_candidate_cap():
    out = read()
    d = row(out, "delta")
    assert (d["low"], d["high"], d["source"], d["state"]) == (-8.0, 10.0, "guidance band", "over")
    t = row(out, "theta")
    assert (t["low"], t["high"], t["source"]) == (10.0, 30.0, "guidance band") and t["state"] == "within"
    bp = row(out, "buying_power")
    assert bp["high"] == 0.3 and bp["state"] == "near"  # 0.27 is past 80% of 0.30
    name = row(out, "per_name")
    assert (
        name["label"].endswith("(AAA)")
        and name["value"] == pytest.approx(0.12)
        and name["source"] == "candidate cap"
    )
    assert name["high"] == _rules.DEFAULT_RULES["portfolio"]["candidates"]["notional_per_name_pct"]["cash"]
    assert row(out, "dollar_delta")["state"] == "no limit" and row(out, "vega")["state"] == "no limit"
    assert row(out, "dollar_delta")["value"] == pytest.approx(-6000.0)


def test_your_limits_win_and_say_so():
    out = read({"delta_bwd": 15, "vega": 30, "theta_day": 22, "buying_power_pct": 0.5, "per_name_pct": 0.1})
    assert row(out, "delta")["source"] == "yours" and row(out, "delta")["state"] == "near"  # 12 of 15
    assert row(out, "vega")["state"] == "over"  # -40 past -30
    assert row(out, "theta")["high"] == 22 and row(out, "theta")["state"] == "near"
    assert row(out, "buying_power")["state"] == "within"
    assert row(out, "per_name")["state"] == "over"


def test_the_day_s_loss_against_its_alert():
    # (1.0 - 1.5) x -1 x 100 + (0.4 - 0.5) x 1 x 100 = +50 - 10 = +40.
    out = read()
    assert out["day_loss"] == {"pnl": 40.0, "alert": -200.0, "crossed": False, "why": None}
    losing = [{"underlying": "AAA", "legs": [{**LEGS[0]["legs"][0], "mid": 4.0}]}]  # -250
    out = read({"day_loss_alert": 150}, legs=losing)
    assert out["day_loss"]["alert"] == -150.0 and out["day_loss"]["crossed"] is True
    assert row(out, "day_loss")["state"] == "over" and row(out, "day_loss")["source"] == "yours"


def test_a_leg_with_no_mark_makes_the_day_unknown_never_zero():
    legs = [{"underlying": "AAA", "legs": [{**LEGS[0]["legs"][0], "mid": None}]}]
    out = read(legs=legs)
    assert out["day_loss"]["pnl"] is None and out["day_loss"]["crossed"] is None
    assert "has no mark" in out["day_loss"]["why"] and row(out, "day_loss")["state"] == "unknown"


def test_no_band_and_no_limit_is_unknown_or_no_limit():
    out = read(book={**BOOK, "bands": {}, "bwd": None})
    assert row(out, "delta")["state"] == "unknown"
