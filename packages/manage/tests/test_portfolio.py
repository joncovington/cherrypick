"""The account as one book: greeks, beta weighting, the break-even move, the stress line and the bands."""

import copy
import math

import pytest

from cherrypick.manage import portfolio, pricing
from cherrypick.manage import rules as _rules

RULES = _rules.DEFAULT_RULES
BALANCES = {"net_liquidating_value": "10000", "used_derivative_buying_power": "4000"}


def _leg(qty, right=None, strike=None, dte=30, iv=0.20, mid=1.0, extrinsic=None, priced=True):
    return {
        "symbol": f"{right or 'SH'}{strike}",
        "label": f"{qty:+g} {strike}{right or ' shares'}",
        "quantity": qty,
        "right": right,
        "strike": strike,
        "dte": dte if right else None,
        "years": max(dte, 0) / 365.0 if right else 0.0,
        "iv": iv if right else None,
        "mid": mid,
        "extrinsic": extrinsic,
        "priced": priced,
    }


def _trade(underlying, spot, legs):
    return {"underlying": underlying, "spot": spot, "legs": legs}


def _metrics(**betas):
    out = {"SPY": {"last": "500", "beta": "1.0", "corr_spy_3month": "1.0", "iv_index": "0.20"}}
    for sym, (beta, corr) in betas.items():
        out[sym] = {"beta": str(beta), "corr_spy_3month": str(corr)}
    return out


def _read(trades, metrics=None, rules=RULES, balances=BALANCES):
    return portfolio.reading(trades, metrics or _metrics(), "SPY", balances, None, rules)


# --------------------------------------------------------------------------- the greeks themselves
@pytest.mark.parametrize("right", ["C", "P"])
def test_greeks_agree_with_the_pricer_they_come_from(right):
    s, k, t, v, r = 100.0, 95.0, 30 / 365, 0.25, 0.04
    g = pricing.greeks(s, k, t, v, right, r)
    h = 0.01
    assert g["delta"] == pytest.approx(
        (pricing.price(s + h, k, t, v, right, r) - pricing.price(s - h, k, t, v, right, r)) / (2 * h),
        rel=1e-4,
    )
    assert g["gamma"] == pytest.approx(
        (pricing.delta(s + h, k, t, v, right, r) - pricing.delta(s - h, k, t, v, right, r)) / (2 * h),
        rel=1e-3,
    )
    day = 1 / 365
    assert g["theta"] == pytest.approx(
        pricing.price(s, k, t - day, v, right, r) - pricing.price(s, k, t, v, right, r), rel=2e-2
    )
    assert g["vega"] == pytest.approx(
        (pricing.price(s, k, t, v + 0.005, right, r) - pricing.price(s, k, t, v - 0.005, right, r)), rel=1e-3
    )


def test_a_short_options_breakeven_is_its_implied_move_at_zero_rate():
    # theta = -1/2 gamma S^2 sigma^2 at r = 0, so a book of one short SPY option breaks even on exactly
    # the move its own IV implies over a day. A units slip anywhere in theta or gamma breaks this.
    rules = copy.deepcopy(RULES)
    rules["pricing"]["rate"] = 0.0
    r = _read([_trade("SPY", 500.0, [_leg(-1, "P", 490.0, iv=0.20)])], rules=rules)
    assert r["breakeven"]["kind"] == "short_gamma"
    assert r["breakeven"]["pct"] == pytest.approx(0.20 * math.sqrt(1 / 365), rel=1e-3)


def test_a_high_beta_names_breakeven_is_its_own_divided_by_beta():
    # XYZ moves 2x SPY, so the SPY move that wipes out its theta is half XYZ's own implied move. This
    # pins gamma's (beta x spot / benchmark spot)^2 scaling, which a SPY-only book cannot see.
    rules = copy.deepcopy(RULES)
    rules["pricing"]["rate"] = 0.0
    r = _read([_trade("XYZ", 50.0, [_leg(-1, "P", 49.0, iv=0.40)])], _metrics(XYZ=(2.0, 0.9)), rules=rules)
    assert r["breakeven"]["pct"] == pytest.approx(0.40 * math.sqrt(1 / 365) / 2.0, rel=1e-3)


# --------------------------------------------------------------------------- beta weighting
def test_beta_weighted_delta_scales_by_beta_and_price():
    r = _read([_trade("XYZ", 50.0, [_leg(100, None, None)])], _metrics(XYZ=(2.0, 0.8)))
    # 100 shares of a $50, beta-2 stock move like 100 x 2 x 50 / 500 = 20 SPY shares.
    assert r["bwd"] == pytest.approx(20.0)
    assert r["bwd_dollars_per_1pct"] == pytest.approx(100.0)
    assert r["complete"]


def test_a_missing_beta_is_named_never_counted_as_zero():
    trades = [_trade("XYZ", 50.0, [_leg(100)]), _trade("ABC", 20.0, [_leg(100)])]
    r = _read(trades, _metrics(XYZ=(2.0, 0.8)))
    assert r["no_beta"] == ["ABC"]
    assert not r["complete"]
    abc = next(u for u in r["underlyings"] if u["underlying"] == "ABC")
    assert abc["bwd"] is None and abc["bwd_share"] is None
    assert r["bwd"] == pytest.approx(20.0)


def test_an_unpriced_leg_is_named_and_left_out():
    legs = [_leg(-1, "P", 45.0), _leg(1, "P", 40.0, priced=False)]
    r = _read([_trade("XYZ", 50.0, legs)], _metrics(XYZ=(1.0, 0.9)))
    assert r["unmodelled_legs"] == [legs[1]["label"]]
    assert not r["complete"]


def test_a_leg_expiring_today_is_taken_at_its_moneyness():
    itm, otm = (
        _leg(-1, "C", 45.0, dte=0, mid=5.30, extrinsic=0.30),
        _leg(-1, "C", 55.0, dte=0, mid=0.05, extrinsic=0.05),
    )
    r = _read([_trade("XYZ", 50.0, [itm, otm])], _metrics(XYZ=(1.0, 0.9)))
    xyz = r["underlyings"][0]
    assert xyz["delta_shares"] == pytest.approx(-100.0)
    assert xyz["theta"] == pytest.approx(35.0)  # both legs' remaining extrinsic decays today
    assert r["complete"]


def test_the_benchmark_missing_leaves_no_beta_weighted_figures():
    r = portfolio.reading(
        [_trade("XYZ", 50.0, [_leg(100)])],
        _metrics(XYZ=(2.0, 0.8)) | {"SPY": {}},
        "SPY",
        BALANCES,
        None,
        RULES,
    )
    assert r["bwd"] is None and r["bwd_dollars_per_1pct"] is None and not r["complete"]


# --------------------------------------------------------------------------- doubts and stress
def test_a_beta_that_disagrees_with_its_correlation_is_doubted():
    trades = [_trade(s, 50.0, [_leg(100)]) for s in ("FXI", "RTX", "OK")]
    r = _read(trades, _metrics(FXI=(0.27, -0.75), RTX=(-0.01, 0.94), OK=(1.1, 0.8)))
    doubted = {d["underlying"] for d in r["beta_doubts"]}
    assert doubted == {"FXI", "RTX"}


def test_the_stress_line_moves_each_name_by_its_beta():
    r = _read([_trade("XYZ", 50.0, [_leg(100)])], _metrics(XYZ=(1.5, 0.8)))
    down = next(s for s in r["stress"] if s["shock"] == -0.10)
    assert down["pnl"] == pytest.approx(100 * 50.0 * -0.15)
    assert down["pct_net_liq"] == pytest.approx(-0.075)


# --------------------------------------------------------------------------- guidance
def _rules_fired(r):
    return [g["rule"] for g in r["guidance"]]


def test_theta_above_its_ceiling_fires_and_inside_the_band_does_not():
    # A short at-the-money SPY put collects far more than 0.3% of a $10,000 account a day.
    heavy = _read([_trade("SPY", 500.0, [_leg(-1, "P", 500.0, dte=7)])])
    assert "portfolio.theta" in _rules_fired(heavy)
    rules = copy.deepcopy(RULES)
    rules["portfolio"]["theta"]["max_pct_nlv"] = 1.0
    rules["portfolio"]["theta"]["min_pct_nlv"] = 0.0
    assert "portfolio.theta" not in _rules_fired(
        _read([_trade("SPY", 500.0, [_leg(-1, "P", 500.0, dte=7)])], rules=rules)
    )


def test_delta_outside_its_band_names_the_trades_pushing_it_out():
    trades = [
        _trade("XYZ", 50.0, [_leg(300)]),
        _trade("ABC", 50.0, [_leg(100)]),
        _trade("HED", 50.0, [_leg(-50)]),
    ]
    r = _read(trades, _metrics(XYZ=(1.0, 0.9), ABC=(1.0, 0.9), HED=(1.0, 0.9)))
    fired = next(g for g in r["guidance"] if g["rule"] == "portfolio.delta")
    assert [d["underlying"] for d in fired["drivers"]] == ["XYZ", "ABC"]  # the short hedge is not a driver


def test_delta_inside_its_band_is_quiet():
    r = _read([_trade("XYZ", 50.0, [_leg(10)])], _metrics(XYZ=(1.0, 0.9)))
    lo, hi = portfolio.delta_band(r, RULES["portfolio"]["delta"])
    assert lo <= r["bwd"] <= hi
    assert "portfolio.delta" not in _rules_fired(r)
    assert r["bands"]["delta"]["inside"] is True
    assert (r["bands"]["delta"]["low"], r["bands"]["delta"]["high"]) == (round(lo, 2), round(hi, 2))


def test_the_stated_bands_agree_with_the_guidance():
    # A surface draws the band from `bands` and never re-derives it, so the two must not disagree.
    out = _read([_trade("XYZ", 50.0, [_leg(300)])], _metrics(XYZ=(1.0, 0.9)))
    assert out["bands"]["delta"]["inside"] is False and "portfolio.delta" in _rules_fired(out)
    assert out["bands"]["delta"]["basis"] == "net_liq"  # no theta to scale by
    heavy = _read([_trade("SPY", 500.0, [_leg(-1, "P", 500.0, dte=7)])])
    assert heavy["bands"]["theta"]["inside"] is False and "portfolio.theta" in _rules_fired(heavy)
    assert heavy["bands"]["delta"]["basis"] == "theta"


def test_breakeven_under_the_expected_move_fires():
    # One short SPY option at the market's own IV breaks even right at the implied move; a book
    # selling options at a lower IV than the benchmark expects breaks even inside it.
    r = _read([_trade("SPY", 500.0, [_leg(-1, "P", 500.0, iv=0.12)])])
    assert "portfolio.breakeven" in _rules_fired(r)


def test_concentration_fires_and_a_position_can_be_excused():
    trades = [_trade(s, 50.0, [_leg(q)]) for s, q in (("XYZ", 300), ("ABC", 100), ("DEF", 100), ("GHI", 100))]
    metrics = _metrics(XYZ=(1.0, 0.9), ABC=(1.0, 0.9), DEF=(1.0, 0.9), GHI=(1.0, 0.9))
    fired = next(g for g in _read(trades, metrics)["guidance"] if g["rule"] == "portfolio.concentration")
    assert [d["underlying"] for d in fired["drivers"]] == ["XYZ"]
    rules, _ = _rules.effective({"positions": {"XYZ": {"disable": ["portfolio.concentration"]}}})
    assert "portfolio.concentration" not in _rules_fired(_read(trades, metrics, rules=rules))


def test_the_portfolio_switch_turns_all_guidance_off():
    rules, warnings = _rules.effective({"portfolio": {"enabled": False}})
    assert warnings == []
    assert _read([_trade("SPY", 500.0, [_leg(-1, "P", 500.0, dte=7)])], rules=rules)["guidance"] == []
