import pytest
from test_evaluate import fired, leg, run

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
