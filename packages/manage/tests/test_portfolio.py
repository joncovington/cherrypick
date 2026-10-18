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


# --------------------------------------------------------------------------- scenarios
def _cell(r, iv_shift, move):
    row = next(m for m in r["scenarios"]["matrix"] if m["iv_shift"] == iv_shift)
    return row["pnl"][r["scenarios"]["moves"].index(move)]


def test_the_matrix_moves_each_name_by_its_beta_and_is_zero_at_its_centre():
    r = _read([_trade("XYZ", 50.0, [_leg(100)])], _metrics(XYZ=(1.5, 0.8)))
    assert _cell(r, 0.0, 0.0) == 0.0
    assert _cell(r, 0.0, -0.10) == pytest.approx(100 * 50.0 * -0.15)
    assert _cell(r, 0.05, 0.02) == pytest.approx(100 * 50.0 * 0.03)  # shares have no vega


def test_a_short_option_loses_when_iv_rises_and_gains_when_it_falls():
    r = _read([_trade("SPY", 500.0, [_leg(-1, "P", 480.0)])])
    assert _cell(r, 0.05, 0.0) < 0 < _cell(r, -0.05, 0.0)


def test_an_iv_shift_never_takes_a_vol_below_its_floor():
    # 20% IV shifted down 25 points would be negative; it is held at the floor, not priced at zero vol.
    rules = copy.deepcopy(RULES)
    rules["portfolio"]["scenarios"]["iv_shifts"] = [-0.25]
    # At the money, so the floor's price differs from the intrinsic value a zero vol would give.
    leg = _leg(-1, "P", 500.0)
    r = _read([_trade("SPY", 500.0, [leg])], rules=rules)
    floor_value = pricing.price(500.0, 500.0, leg["years"], portfolio.MIN_VOL, "P", 0.04)
    assert floor_value * 100 - pricing.intrinsic(500.0, 500.0, "P") * 100 > 1.0
    now = pricing.price(500.0, 500.0, leg["years"], 0.20, "P", 0.04)
    assert _cell(r, -0.25, 0.0) == pytest.approx(-100 * (floor_value - now), abs=0.01)


def test_the_risk_graph_at_expiry_keeps_an_out_of_the_money_shorts_whole_premium():
    leg = _leg(-1, "P", 450.0, dte=20)
    r = _read([_trade("SPY", 500.0, [leg])])
    graph = r["scenarios"]["graph"]
    now, expiry = graph["curves"]
    assert now["horizon_days"] == 0 and expiry["horizon_days"] == 20
    flat = graph["moves"].index(0.0)
    assert now["pnl"][flat] == 0.0
    premium = pricing.price(500.0, 450.0, leg["years"], 0.20, "P", 0.04) * 100
    assert expiry["pnl"][flat] == pytest.approx(premium, abs=0.01)
    # 15% down at expiry: the put is 25 points in the money.
    assert expiry["pnl"][0] == pytest.approx(premium - 25.0 * 100, abs=0.01)


def test_the_term_weighted_shift_moves_front_months_more_than_leaps():
    flat = copy.deepcopy(RULES)
    flat["portfolio"]["scenarios"]["iv_shift_weighting"] = "flat"
    leaps = [_trade("SPY", 500.0, [_leg(1, "C", 500.0, dte=360)])]
    front = [_trade("SPY", 500.0, [_leg(1, "C", 500.0, dte=7)])]
    term_r, flat_r = _read(leaps), _read(leaps, rules=flat)
    assert (
        term_r["scenarios"]["iv_shift_weighting"] == "term"
        and flat_r["scenarios"]["iv_shift_weighting"] == "flat"
    )
    assert 0 < _cell(term_r, 0.05, 0.0) < _cell(flat_r, 0.05, 0.0)  # sqrt(30/360): a LEAPS's IV moves less
    assert (
        _cell(_read(front), 0.05, 0.0) > _cell(_read(front, rules=flat), 0.05, 0.0) > 0
    )  # and a 7-day one more
    # A 30-day leg is the reference: weighted or not, the same.
    month = [_trade("SPY", 500.0, [_leg(1, "C", 500.0, dte=30)])]
    assert _cell(_read(month), 0.05, 0.0) == pytest.approx(_cell(_read(month, rules=flat), 0.05, 0.0))


def test_a_name_without_beta_is_left_out_of_the_scenarios_and_named():
    r = _read([_trade("XYZ", 50.0, [_leg(100)]), _trade("ABC", 20.0, [_leg(100)])], _metrics(XYZ=(1.0, 0.9)))
    assert r["scenarios"]["left_out"] == ["ABC"]
    assert _cell(r, 0.0, 0.10) == pytest.approx(100 * 50.0 * 0.10)


# --------------------------------------------------------------------------- the expiry ladder
def test_the_ladder_groups_trades_by_their_nearest_short_and_sums_what_they_can_lose():
    t1 = _trade("AAA", 50.0, [_leg(-1, "P", 49.0, dte=7)])
    t1.update(trade_id="AAA", nearest_short_expiry="2026-10-23", profile={"downside_from_now": -300.0})
    t2 = _trade("BBB", 50.0, [_leg(-1, "P", 49.0, dte=7)])
    t2.update(
        trade_id="BBB",
        nearest_short_expiry="2026-10-23",
        profile=None,
        earnings={"before_nearest_short_expiry": True},
    )
    t3 = _trade("CCC", 50.0, [_leg(-1, "P", 45.0, dte=35)])
    t3.update(
        trade_id="CCC",
        nearest_short_expiry="2026-11-20",
        profile={"downside_from_now": -500.0},
        earnings={"before_nearest_short_expiry": False},
    )
    for t in (t1, t2, t3):
        for leg in t["legs"]:
            leg["expiration"] = t["nearest_short_expiry"]
    r = _read([t1, t2, t3], _metrics(AAA=(1.0, 0.9), BBB=(1.0, 0.9), CCC=(1.0, 0.9)))
    first, second = r["ladder"]["expiries"]
    assert first["expiration"] == "2026-10-23" and first["trades"] == ["AAA", "BBB"]
    assert first["downside"] == -300.0 and first["downside_unknown"] == 1  # BBB's unknown, never zero
    assert second["downside"] == -500.0
    assert first["theta"] > second["theta"] > 0  # the 7-day shorts decay faster
    earn = r["ladder"]["earnings"]
    assert earn["trades"] == ["BBB"]
    assert earn["theta_share"] == pytest.approx(first["theta"] / 2 / r["theta"], rel=1e-2)


# --------------------------------------------------------------------------- buying power by VIX
@pytest.mark.parametrize(
    ("vix", "cap"), [(12.0, 0.25), (15.0, 0.30), (19.99, 0.30), (25.0, 0.35), (45.0, 0.50)]
)
def test_the_buying_power_cap_steps_up_with_vix(vix, cap):
    assert portfolio.bp_cap(vix, RULES["portfolio"]["buying_power"]["vix_steps"]) == cap


def test_buying_power_over_its_vix_cap_fires_and_without_vix_there_is_no_band():
    trades = [_trade("XYZ", 50.0, [_leg(10)])]
    over = _read(trades, _metrics(XYZ=(1.0, 0.9)) | {"VIX": {"last": "19.99"}})  # 40% used vs a 30% cap
    assert over["bands"]["buying_power"] == {
        "low": 0.0,
        "high": 0.30,
        "value": 0.4,
        "vix": 19.99,
        "inside": False,
    }
    assert "portfolio.buying_power" in _rules_fired(over)
    calm = _read(trades, _metrics(XYZ=(1.0, 0.9)) | {"VIX": {"last": "45"}})
    assert calm["bands"]["buying_power"]["inside"] is True and "portfolio.buying_power" not in _rules_fired(
        calm
    )
    blind = _read(trades, _metrics(XYZ=(1.0, 0.9)))
    assert blind["bands"]["buying_power"]["high"] is None and blind["bands"]["buying_power"]["inside"] is None
    assert "portfolio.buying_power" not in _rules_fired(blind)
