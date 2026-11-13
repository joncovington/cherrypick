"""A diagonal's take profit is capped at a share of what it can make by the short's expiry.

At +75% of the debit a PMCC's target sat past its whole call-away value (the fixture's $10.00 debit on
a $5-wide spread asks for $17.50), so its take profit could never fire.
"""

from datetime import date

from test_evaluate import fired, leg
from test_rolls_weekly_short import opened, run

from cherrypick.manage import rules as _rules

SESSION = date(2026, 11, 30)


def narrow(long_mid=11.0, short_mid=2.0):
    # Long 95C to 2027-01-15 bought at 12.00, short 100C to 2026-12-04 sold at 2.00: 10.00 in, 5 wide.
    return [leg(1, "C", 95, "2027-01-15", 12.0, long_mid), leg(-1, "C", 100, "2026-12-04", 2.0, short_mid)]


CHAIN = opened(short_exp="2026-12-04", strike=100)
# Spot well over the short: the spread near its width at the short's expiry.
DEEP = {"legs": narrow(long_mid=21.0, short_mid=6.2), "chain": CHAIN, "spot": 106.0}


def test_a_target_past_the_diagonals_reach_is_capped_at_three_quarters_of_its_modelled_gain():
    t = run(SESSION, **DEEP)
    tp = t["exit_levels"]["take_profit"]
    cap = tp["capped_by_max_gain"]
    assert cap is not None and cap["modelled"] and cap["share"] == 0.75
    assert tp["target"] < 0.75
    assert abs(tp["target"] * 1000.0 - 0.75 * cap["max_gain"]) < 0.01


def test_the_capped_target_fires_where_the_debit_target_never_could():
    t = run(SESSION, **DEEP)
    assert t["capture"] < 0.75  # under the old +75% target
    assert "debit.take_profit" in fired(t)
    reason = next(f["reason"] for f in t["fired"] if f["rule"] == "debit.take_profit")
    assert "of the $" in reason and "(modelled)" in reason


def test_a_diagonal_that_cannot_gain_by_the_shorts_expiry_is_left_to_the_width_check():
    # Modelled to lose wherever the stock ends by the short's expiry: no positive gain to cap at.
    t = run(SESSION, legs=narrow(), chain=CHAIN)
    assert t["profile"]["max_gain_from_open"] < 0
    assert t["exit_levels"]["take_profit"]["capped_by_max_gain"] is None
    assert "debit.diagonal_width" in fired(t)


def test_a_cap_above_the_debit_target_leaves_it_alone():
    loose, _ = _rules.effective({"debit": {"take_profit": {"max_gain_cap": {"share": 50.0}}}})
    t = run(SESSION, rules=loose, **DEEP)
    tp = t["exit_levels"]["take_profit"]
    assert tp["capped_by_max_gain"] is None and tp["target"] == 0.75


def test_shapes_not_listed_keep_their_own_target():
    none, _ = _rules.effective({"debit": {"take_profit": {"max_gain_cap": {"shapes": []}}}})
    t = run(SESSION, rules=none, **DEEP)
    assert t["exit_levels"]["take_profit"]["capped_by_max_gain"] is None
