"""Needs a look (attention.py): each short option against its strike, and what expires soon.

A short within 3% of its strike is near, past it in the money, and a call likely to be exercised for
its dividend (or any short with almost no time value in the money) is early exercise; longs and
far-out shorts are not listed; the live loops' 0DTE legs are left out; a cash-settled index is never
early. Synthetic names and prices, as in test_wheel.py.
"""

from datetime import date

from test_wheel import occ, opt_leg

from cherrypick.manage import attention
from cherrypick.manage import rules as _rules

SESSION = date(2026, 11, 4)


def read(legs, spot=50.0, name="XYZ", metrics=None, rules=None):
    under = [{"underlying": name, "legs": legs}]
    m = {name: {"last": spot, **(metrics or {})}}
    return attention.reading(under, m, SESSION, rules or _rules.DEFAULT_RULES)


def test_near_in_the_money_and_far_shorts():
    legs = [
        opt_leg(occ("2026-12-18", "P", 49), -1, 0.90, 1.20),  # 2.0% above: near
        opt_leg(occ("2026-12-18", "P", 52), -1, 2.60, 1.50),  # 3.8% through: in the money
        opt_leg(occ("2026-12-18", "P", 45), -1, 0.30, 0.60),  # 11% away: not listed
        opt_leg(occ("2026-12-18", "C", 51), -1, 1.00, 1.10),  # 2.0% below the call: near
    ]
    out = read(legs)
    got = [(i["leg"], i["level"]) for i in out["items"]]
    # Worst first, then nearest: the call is 1.96% away, the put 2.04%.
    assert got == [("2026-12-18 52P", "in the money"), ("2026-12-18 51C", "near"), ("2026-12-18 49P", "near")]
    near = out["items"][2]
    assert near["distance"] == round((50 - 49) / 49, 4) and near["extrinsic"] == 0.9
    assert "alert at 3%" in near["why"] and out["names"] == ["XYZ"]


def test_a_long_is_never_listed_however_close():
    out = read([opt_leg(occ("2026-12-18", "P", 50), 1, 1.5, 1.0)])
    assert out["items"] == []


def test_a_call_whose_time_value_is_under_the_dividend_before_expiry_is_early_exercise():
    call = opt_leg(occ("2026-12-18", "C", 48), -1, 2.10, 1.00)  # 2.00 intrinsic, 0.10 time value
    out = read([call], metrics={"ex_dividend_date": "2026-11-20", "dividend_rate_per_share": 0.25})
    (i,) = out["items"]
    assert i["level"] == "early exercise" and "ex-dividend 2026-11-20" in i["why"]
    # No ex-date before expiry: plain in the money.
    (i,) = read([call], metrics={"ex_dividend_date": "2027-01-10", "dividend_rate_per_share": 0.25})["items"]
    assert i["level"] == "in the money"
    # Almost no time value at all: early exercise whatever the dividend.
    put = opt_leg(occ("2026-12-18", "P", 53), -1, 3.02, 1.00)
    (i,) = read([put])["items"]
    assert i["level"] == "early exercise" and "under 0.05" in i["why"]


def test_a_cash_settled_index_is_never_early_and_its_0dte_legs_are_left_out():
    deep = opt_leg(occ("2026-12-18", "P", 5200, root="SPX"), -1, 199.0, 50.0)
    today = opt_leg(occ("2026-11-04", "P", 5010, root="SPX"), -1, 1.0, 2.0)
    out = read([deep, today], spot=5000.0, name="SPX")
    assert [(i["leg"], i["level"]) for i in out["items"]] == [("2026-12-18 5200P", "in the money")]
    assert all(e["expiration"] != "2026-11-04" for e in out["expiring"])


def test_expiring_lists_every_leg_within_the_days_long_and_short():
    legs = [
        opt_leg(occ("2026-11-06", "P", 40), 1, 0.05, 0.30),
        opt_leg(occ("2026-11-06", "P", 45), -1, 0.10, 0.80),
        opt_leg(occ("2026-11-20", "P", 45), -1, 0.40, 0.80),
    ]
    out = read(legs)
    assert [(e["leg"], e["dte"], e["quantity"]) for e in out["expiring"]] == [
        ("2026-11-06 40P", 2, 1),
        ("2026-11-06 45P", 2, -1),
    ]


def test_no_spot_or_no_mid_leaves_figures_null_and_lists_nothing():
    out = attention.reading(
        [{"underlying": "XYZ", "legs": [opt_leg(occ("2026-12-18", "P", 50), -1, 1.0, 1.0)]}],
        {},
        SESSION,
        _rules.DEFAULT_RULES,
    )
    assert out["items"] == [] and out["names"] == []


def test_the_threshold_is_the_ruleset_s():
    rules, warnings = _rules.effective({"attention": {"near_pct": 0.05}})
    assert warnings == []
    out = read([opt_leg(occ("2026-12-18", "P", 48), -1, 0.5, 1.0)], rules=rules)  # 4.2% above
    assert [i["level"] for i in out["items"]] == ["near"] and out["near_pct"] == 0.05
