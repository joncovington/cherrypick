import pytest
from test_evaluate import SESSION, fired, leg, run

from cherrypick.manage import evaluate as ev
from cherrypick.manage import shapes

NOV = "2026-11-20"

# The account's real shapes on 2026-10-13.
XLU = [leg(1, "P", 40, NOV, 0.49, 0.275), leg(-1, "P", 37, NOV, 0.07, 0.025),
       leg(-1, "C", 42, NOV, 0.78, 0.92), leg(1, "C", 45, NOV, 0.13, 0.14)]  # fmt: skip
FXI = [leg(1, "P", 33, NOV, 0.81, 0.45), leg(-1, "C", 33, NOV, 1.21, 1.73), leg(1, "C", 34, NOV, 0.72, 1.055)]
GLW = [leg(1, "C", 155, NOV, 17.85, 8.825), leg(-1, "C", 150, NOV, 19.15, 10.875),
       leg(-1, "P", 140, NOV, 13.85, 7.025), leg(1, "P", 125, NOV, 7.15, 2.5)]  # fmt: skip
META = [leg(1, "C", 725, NOV, 51.52, 63.8), leg(-1, "C", 720, NOV, 53.76, 67.425),
        leg(1, "P", 635, NOV, 18.2, 4.575), leg(-1, "P", 655, NOV, 24.05, 6.775)]  # fmt: skip
GLD = [
    leg(1, "C", 410, NOV, 15.17, 2.365),
    leg(-2, "C", 415, NOV, 2.57, 1.825),
    leg(1, "C", 435, NOV, 1.01, 0.705),
]


def cost(legs):
    return sum(x["value"] - x["open_pl"] for x in legs)


def opts(legs):
    return [{**x, "quantity": float(x["quantity"])} for x in legs]


@pytest.mark.parametrize(
    "legs, name",
    [
        (XLU, "super bear"),
        (FXI, "risk reversal (bear)"),
        (GLW, "jade lizard (put wing)"),
        (META, "jade lizard (put wing)"),
        (GLD, "broken-wing butterfly"),
        ([leg(-1, "P", 16, NOV, 1.01, 1.445)], "naked put"),
        ([leg(-1, "P", 95, NOV, 2.0, 1.0), leg(1, "P", 90, NOV, 0.8, 0.4)], "credit spread"),
        (
            [
                leg(-1, "P", 95, NOV, 2.0, 1.0),
                leg(1, "P", 90, NOV, 0.8, 0.4),
                leg(-1, "C", 110, NOV, 1.0, 0.5),
                leg(1, "C", 115, NOV, 0.4, 0.2),
            ],  # fmt: skip
            "iron condor",
        ),
        (
            [
                leg(-1, "P", 95, NOV, 1.0, 1.0),
                leg(1, "P", 90, NOV, 0.3, 0.3),
                leg(1, "C", 105, NOV, 1.2, 1.2),
                leg(-1, "C", 110, NOV, 0.4, 0.4),
            ],  # fmt: skip
            "super bull",
        ),
    ],
)
def test_the_accounts_shapes_are_named(legs, name):
    assert shapes.classify(opts(legs), cost(legs)) == name
    assert name in shapes.NAMES


# The account's SPY ratio on 2026-10-18: +1 745P / -3 740P / +2 730P, $200 in against $1,300 at risk.
SPY_132 = [
    leg(2, "P", 730, NOV, 5.78, 5.565),
    leg(-3, "P", 740, NOV, 7.2333, 6.995),
    leg(1, "P", 745, NOV, 8.14, 7.895),
]
DEC = "2026-12-18"


@pytest.mark.parametrize(
    "legs, name",
    [
        (SPY_132, "1-3-2"),
        ([{**x, "quantity": x["quantity"] * 2} for x in SPY_132], "1-3-2"),  # 2-6-4 reduces
        ([leg(2, "C", 100, NOV, 1, 1), leg(-3, "C", 105, NOV, 1, 1), leg(1, "C", 115, NOV, 1, 1)], "1-3-2"),
        # A naked short (1-3-1) is not a ratio butterfly.
        ([leg(1, "P", 730, NOV, 1, 1), leg(-3, "P", 740, NOV, 1, 1), leg(1, "P", 745, NOV, 1, 1)], "custom"),
        # Across expiries: a ratio time spread.
        (
            [leg(2, "P", 730, DEC, 1, 1), leg(-3, "P", 740, NOV, 1, 1), leg(1, "P", 745, DEC, 1, 1)],
            "ratio time spread",
        ),
        # A 1-2-1 across expiries keeps its diagonal name.
        (
            [leg(1, "P", 730, DEC, 1, 1), leg(-2, "P", 740, NOV, 1, 1), leg(1, "P", 750, DEC, 1, 1)],
            "diagonal",
        ),
    ],
)
def test_a_ratio_butterfly_is_named_by_its_quantities_from_the_top_strike(legs, name):
    assert shapes.classify(opts(legs), cost(legs)) == name


def test_the_spy_1_3_2_is_judged_on_its_risk_not_its_credit():
    t = run(SPY_132, spot=763.28)
    assert t["shape"] == "1-3-2" and t["kind"] == "risk-judged"
    assert "credit.stop" not in fired(t)
    assert t["exit_levels"]["stop"]["basis"] != "credit"
    assert (
        "This is a 1-3-2 ratio butterfly" in t["explanation"]
        and "judged on what it can win" in t["explanation"]
    )


def test_a_super_bear_is_judged_on_its_risk_not_its_tiny_credit():
    t = run(XLU, spot=41.85)
    assert t["kind"] == "directional"
    assert "credit.stop" not in fired(t)  # the old rule closed it on a $30 loss against a $23 credit
    assert t["recommendation"]["action"] == ev.HOLD
    d = t["directional"]
    assert d["max_loss"] == pytest.approx(277, abs=1) and d["max_gain"] == pytest.approx(323, abs=1)
    assert d["target"] == pytest.approx(161.5, abs=1)
    assert "This is a super bear" in t["explanation"]


def test_a_directional_trade_stops_at_half_its_max_loss():
    hurt = [{**x} for x in XLU]
    for x, mid in zip(hurt, (0.05, 0.01, 2.40, 0.60), strict=True):  # XLU near 44
        x["value"] = x["quantity"] * mid * 100
        x["open_pl"] = x["quantity"] * (mid - x["open_price"]) * 100
        x["mid"] = mid
    t = run(hurt, spot=44.0)
    assert t["recommendation"]["rule"] == "directional.stop"


def test_the_risk_reversal_target_is_capped_at_the_risk_taken():
    t = run(FXI, spot=34.165)
    assert t["kind"] == "directional"
    assert t["directional"]["target"] == pytest.approx(t["directional"]["max_loss"])


def test_short_premium_shapes_are_never_directional():
    naked = run([leg(-1, "P", 16, NOV, 1.01, 1.445)], spot=15.225)
    assert naked["kind"] == "credit"
    far_otm = run([leg(-1, "P", 80, NOV, 0.12, 0.10), leg(1, "P", 75, NOV, 0.05, 0.04)], spot=100)
    assert far_otm["kind"] == "credit"


def test_a_jade_lizards_call_side_in_the_money_is_not_a_broken_thesis():
    t = run(META, spot=762.47)
    assert t["shape"] == "jade lizard (put wing)"
    assert "credit.breached" not in fired(t)
    assert "no upside risk" in t["explanation"]


# The tastylive Options Strategy Guide's (2023) own examples, XYZ at $100, priced at the credit or
# debit the guide gives.
JAN = "2027-01-15"
GUIDE = [
    ([leg(-1, "P", 100, NOV, 4.0, 4.0), leg(-1, "C", 100, NOV, 4.0, 4.0)], "short straddle"),
    ([leg(-1, "P", 90, NOV, 1.5, 1.5), leg(-1, "C", 110, NOV, 1.5, 1.5)], "short strangle"),
    # 95P sold, 105/110 call spread sold, over $5.00 in all.
    (
        [leg(-1, "P", 95, NOV, 3.2, 3.2), leg(-1, "C", 105, NOV, 3.0, 3.0), leg(1, "C", 110, NOV, 1.0, 1.0)],
        "jade lizard",
    ),  # fmt: skip
    # 100P sold, 100/105 call spread sold, over $5.00.
    (
        [leg(-1, "P", 100, NOV, 4.0, 4.0), leg(-1, "C", 100, NOV, 4.0, 4.0), leg(1, "C", 105, NOV, 2.4, 2.4)],
        "big lizard",
    ),  # fmt: skip
    # 110C sold, 95/90 put spread sold, over $5.00.
    (
        [leg(-1, "C", 110, NOV, 3.0, 3.0), leg(-1, "P", 95, NOV, 3.2, 3.2), leg(1, "P", 90, NOV, 1.0, 1.0)],
        "reverse jade lizard",
    ),  # fmt: skip
    # 100C sold, 100/95 put spread sold, over $5.00.
    (
        [leg(-1, "C", 100, NOV, 4.0, 4.0), leg(-1, "P", 100, NOV, 4.0, 4.0), leg(1, "P", 95, NOV, 2.4, 2.4)],
        "reverse big lizard",
    ),  # fmt: skip
    # An iron condor taken in for its put side's width: a reverse jade lizard with a call wing.
    (
        [
            leg(-1, "P", 95, NOV, 4.0, 4.0),
            leg(1, "P", 93, NOV, 2.5, 2.5),
            leg(-1, "C", 105, NOV, 2.0, 2.0),
            leg(1, "C", 110, NOV, 1.2, 1.2),
        ],
        "reverse jade lizard (call wing)",
    ),  # fmt: skip
    # Dynamic width: 97/90 puts and 107/110 calls, $2.00 -- still an iron condor.
    (
        [
            leg(-1, "P", 97, NOV, 2.0, 2.0),
            leg(1, "P", 90, NOV, 0.6, 0.6),
            leg(-1, "C", 107, NOV, 1.0, 1.0),
            leg(1, "C", 110, NOV, 0.4, 0.4),
        ],
        "iron condor",
    ),  # fmt: skip
    ([leg(2, "C", 95, NOV, 7.0, 7.0), leg(-1, "C", 100, NOV, 4.0, 4.0)], "call zebra"),
    ([leg(2, "P", 105, NOV, 7.0, 7.0), leg(-1, "P", 100, NOV, 4.0, 4.0)], "put zebra"),
    ([leg(1, "C", 105, NOV, 2.0, 2.0), leg(-2, "C", 110, NOV, 1.1, 1.1)], "call front-ratio"),
    ([leg(1, "P", 95, NOV, 2.0, 2.0), leg(-2, "P", 90, NOV, 1.1, 1.1)], "put front-ratio"),
    (
        [
            leg(1, "C", 102, NOV, 3.0, 3.0),
            leg(-1, "C", 104, NOV, 2.2, 2.2),
            leg(-1, "C", 110, NOV, 0.6, 0.6),
            leg(1, "C", 114, NOV, 0.2, 0.2),
        ],
        "broken-heart butterfly",
    ),  # fmt: skip
    (
        [
            leg(1, "P", 98, NOV, 3.0, 3.0),
            leg(-1, "P", 96, NOV, 2.2, 2.2),
            leg(-1, "P", 90, NOV, 0.6, 0.6),
            leg(1, "P", 86, NOV, 0.2, 0.2),
        ],
        "broken-heart butterfly",
    ),  # fmt: skip
    (
        [leg(1, "P", 100, NOV, 4.0, 4.0), leg(-2, "P", 97, NOV, 2.6, 2.6), leg(1, "P", 91, NOV, 1.0, 1.0)],
        "broken-wing butterfly",
    ),  # fmt: skip
    ([leg(1, "C", 90, JAN, 14.0, 14.0), leg(-1, "C", 105, NOV, 1.5, 1.5)], "diagonal"),
    ([leg(1, "C", 105, JAN, 4.0, 4.0), leg(-1, "C", 105, NOV, 1.5, 1.5)], "calendar"),
]


@pytest.mark.parametrize("legs, name", GUIDE)
def test_the_strategy_guides_shapes_are_named(legs, name):
    assert shapes.classify(opts(legs), cost(legs)) == name
    assert name in shapes.NAMES


@pytest.mark.parametrize(
    "legs",
    [
        # Equal widths, disconnected: a condor, not a broken heart.
        [
            leg(1, "C", 102, NOV, 3, 3),
            leg(-1, "C", 104, NOV, 2, 2),
            leg(-1, "C", 110, NOV, 1, 1),
            leg(1, "C", 112, NOV, 1, 1),
        ],  # fmt: skip
        # A lizard taken in for less than its spread's width keeps risk on both sides.
        [leg(-1, "C", 110, NOV, 1.0, 1.0), leg(-1, "P", 95, NOV, 2.0, 2.0), leg(1, "P", 90, NOV, 1.0, 1.0)],
        # Two longs against a short nearer the money is a back-spread, not a zebra.
        [leg(-1, "C", 95, NOV, 7.0, 7.0), leg(2, "C", 100, NOV, 4.0, 4.0)],
        # An inverted strangle (the put above the call).
        [leg(-1, "P", 105, NOV, 6.0, 6.0), leg(-1, "C", 95, NOV, 6.0, 6.0)],
    ],
)
def test_near_misses_stay_custom(legs):
    assert shapes.classify(opts(legs), cost(legs)) == "custom"


def test_every_shape_a_default_target_names_is_one_classify_returns():
    for section in ("credit", "debit"):
        for shape in ev._rules.DEFAULT_RULES[section]["take_profit"]["by_shape"]:
            assert shape in shapes.NAMES, shape


def test_a_short_straddle_takes_profit_at_a_quarter_of_its_credit():
    # $800 in at the 100 strike, now $560 to buy back: 30% kept.
    t = run([leg(-1, "P", 100, NOV, 4.0, 2.8), leg(-1, "C", 100, NOV, 4.0, 2.8)])
    assert t["shape"] == "short straddle"
    assert fired(t)["credit.take_profit"] == ev.TAKE_PROFIT
    assert t["recommendation"]["rule"] == "credit.take_profit"
    assert t["recommendation"]["confidence"] == 55
    assert t["exit_levels"]["take_profit"]["by_shape"] == "short straddle"
    assert "a quarter of the credit" in t["explanation"] and "target is its own" in t["explanation"]


def test_an_iron_condor_keeps_the_half_credit_target():
    t = run([leg(-1, "P", 95, NOV, 2.0, 1.4), leg(1, "P", 90, NOV, 0.8, 0.56),
             leg(-1, "C", 110, NOV, 1.0, 0.7), leg(1, "C", 115, NOV, 0.4, 0.28)])  # fmt: skip
    assert t["shape"] == "iron condor" and t["capture"] == pytest.approx(0.30)
    assert "credit.take_profit" not in fired(t)
    assert t["exit_levels"]["take_profit"]["target"] == 0.5
    assert t["exit_levels"]["take_profit"]["by_shape"] is None


def test_a_calendar_takes_profit_at_a_quarter_of_its_debit():
    t = run([leg(1, "C", 105, JAN, 3.0, 3.5), leg(-1, "C", 105, NOV, 1.5, 1.6)])
    assert t["shape"] == "calendar" and t["capture"] == pytest.approx(40 / 150)
    assert fired(t)["debit.take_profit"] == ev.TAKE_PROFIT
    assert "a quarter or more on what you paid" in t["explanation"]


def test_a_reverse_jade_lizards_put_side_in_the_money_is_not_a_broken_thesis():
    legs = [leg(-1, "C", 110, NOV, 2.6, 0.1), leg(-1, "P", 95, NOV, 3.2, 3.0), leg(1, "P", 90, NOV, 0.7, 0.8)]
    t = run(legs, spot=93)
    assert t["shape"] == "reverse jade lizard"
    assert "credit.breached" not in fired(t)
    # The same legs taken in for less than the put spread's width carry downside risk, and are judged.
    short = [{**x} for x in legs]
    short[0].update(open_price=1.0, open_pl=-1 * (0.1 - 1.0) * 100)
    t = run(short, spot=93)
    assert t["shape"] == "custom" and fired(t)["credit.breached"] == ev.CLOSE


def test_a_broken_heart_butterfly_bought_cheaply_is_judged_on_its_tail():
    t = run([leg(1, "C", 102, NOV, 3.0, 3.0), leg(-1, "C", 104, NOV, 2.2, 2.2),
             leg(-1, "C", 110, NOV, 0.6, 0.6), leg(1, "C", 114, NOV, 0.2, 0.2)])  # fmt: skip
    assert t["shape"] == "broken-heart butterfly" and t["kind"] == "risk-judged"
    assert "broken-heart butterfly" in t["explanation"]


def _width_reason(t):
    return next(f for f in t["fired"] if f["rule"] == "debit.diagonal_width")["reason"]


def test_a_diagonal_whose_long_expires_soon_after_the_short_is_judged_on_its_width():
    # The 90 call in December under the 105 call in November, $15 apart: the long has a month left at
    # the short's expiry, so the spread is worth about the width at best.
    heavy = run([leg(1, "C", 90, DEC, 13.0, 13.0), leg(-1, "C", 105, NOV, 1.0, 1.0)])
    assert heavy["shape"] == "diagonal"
    assert fired(heavy)["debit.diagonal_width"] == ev.REVIEW  # $1,200 in, 80% of $1,500
    assert heavy["diagonal_check"]["basis"] == "modelled"
    assert heavy["diagonal_check"]["best"] == pytest.approx(1500, abs=100)
    reason = _width_reason(heavy)
    assert "best case is $1,563, 30% over the $1,200 net debit (at least 33%)" in reason
    assert "little left to make" in reason
    assert "short's expiry" in heavy["explanation"]
    light = run([leg(1, "C", 90, DEC, 11.0, 11.0), leg(-1, "C", 105, NOV, 1.0, 1.0)])
    assert "debit.diagonal_width" not in fired(light)  # $1,000 in, 67%
    puts = run([leg(1, "P", 110, DEC, 13.0, 13.0), leg(-1, "P", 95, NOV, 1.0, 1.0)])
    assert fired(puts)["debit.diagonal_width"] == ev.REVIEW  # the put mirror, $1,200 in
    assert puts["diagonal_check"]["run_at"] < 95  # past a put short is below it
    off = ev._rules.effective({"positions": {"XYZ": {"disable": ["debit.diagonal_width"]}}})[0]
    assert "debit.diagonal_width" not in fired(
        run([leg(1, "C", 90, DEC, 13.0, 13.0), leg(-1, "C", 105, NOV, 1.0, 1.0)], rules=off)
    )


def test_a_diagonal_whose_long_has_years_left_keeps_time_value_the_width_ignores():
    # The same strikes and the same $1,200 net, 80% of the width, with the long in December 2028: at
    # the short's expiry the long still has two years of time value, and the spread's best case is far
    # over the width. The width rule flagged this; the modelled one does not.
    far = run([leg(1, "C", 90, "2028-12-15", 22.0, 22.0), leg(-1, "C", 105, NOV, 10.0, 10.0)])
    assert far["cost"] == 1200 and far["diagonal_check"]["width"] == 1500
    assert far["diagonal_check"]["best"] > 1500 / 0.75
    assert far["diagonal_check"]["run_value"] > far["cost"]
    assert "debit.diagonal_width" not in fired(far)


def test_a_high_yield_names_long_dated_call_is_priced_with_its_dividend():
    # Two years out at a 4% rate and no dividend, a 95 call on a $100 stock is worth at least $12.92;
    # an $11 mid sits under that, so no vol prices it and the check had nothing to model. With the
    # name's 4.5% yield it prices, and the trade, 94% of its $900 width, has plenty left to make.
    legs = [leg(1, "C", 95, "2028-12-15", 11.0, 11.0), leg(-1, "C", 104, NOV, 2.5, 2.5)]
    rules = ev._rules.effective(None)[0]
    bare = ev.evaluate_trade("XYZ", legs, {"last": 100.0}, SESSION, 1e5, rules)
    assert bare["legs"][0]["iv"] is None and bare["diagonal_check"]["basis"] == "width"
    assert fired(bare)["debit.diagonal_width"] == ev.REVIEW
    paid = ev.evaluate_trade("XYZ", legs, {"last": 100.0}, SESSION, 1e5, rules, div_yield=0.045)
    check = paid["diagonal_check"]
    assert check["basis"] == "modelled" and check["div_yield"] == 0.045
    assert check["best"] > paid["cost"] / 0.75 and check["run_value"] > paid["cost"]
    assert "debit.diagonal_width" not in fired(paid)


def test_a_diagonal_that_loses_on_a_run_through_its_short_is_flagged():
    # A narrow $2 diagonal on a rich long: the best case, at the short strike, is 45% over the net, but
    # one expected move past the short the long's time value has gone and the spread is worth less.
    t = run([leg(1, "C", 100, DEC, 8.5, 8.5), leg(-1, "C", 102, NOV, 4.0, 4.0)])
    check = t["diagonal_check"]
    assert not check["thin"] and check["run_loses"]
    assert fired(t)["debit.diagonal_width"] == ev.REVIEW
    assert "a run through the short loses money" in _width_reason(t)
    assert "a run well past the short strike loses money" in t["explanation"]
    # One expected move further out is the knob: none at all puts the run at the strike, the best case.
    rules = ev._rules.effective({"debit": {"diagonal_width": {"run_past_short_moves": 0.0}}})[0]
    assert "debit.diagonal_width" not in fired(
        run([leg(1, "C", 100, DEC, 8.5, 8.5), leg(-1, "C", 102, NOV, 4.0, 4.0)], rules=rules)
    )


def test_a_diagonal_with_nothing_to_model_falls_back_to_the_width():
    t = run([leg(1, "C", 90, DEC, 13.0, 13.0), leg(-1, "C", 105, NOV, 1.0, 1.0)], spot=None)
    assert t["diagonal_check"]["basis"] == "width"
    reason = _width_reason(t)
    assert "80% of the $1,500" in reason and "no IV to model the long" in reason
    assert "could not be priced" in t["explanation"]
    over = run([leg(1, "C", 90, DEC, 17.0, 17.0), leg(-1, "C", 105, NOV, 1.0, 1.0)], spot=None)
    assert "loses money" in _width_reason(over)


def test_a_cheap_broken_wing_butterfly_is_judged_on_its_tail():
    # Priced at the chain's 1.93 net debit (opened 8.33, rolled twice for 6.40), not the open legs.
    chain_basis = [
        leg(1, "C", 410, NOV, 3.96, 2.365),
        leg(-2, "C", 415, NOV, 2.04, 1.825),
        leg(1, "C", 435, NOV, 0.05, 0.705),
    ]
    t = run(chain_basis, spot=381.65, net_liq=15889)
    assert t["kind"] == "risk-judged"
    assert "debit.stop" not in fired(t)
    assert t["recommendation"]["rule"] == "risk.oversize"
    assert "broken-wing butterfly" in t["explanation"]
