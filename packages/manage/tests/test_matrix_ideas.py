"""The risk matrix's horizons and history row (spotvol), and the ideas under it (ideas.py)."""

import copy
import math

import pytest

from cherrypick.manage import portfolio, pricing, spotvol
from cherrypick.manage import rules as _rules

RULES = _rules.DEFAULT_RULES
BALANCES = {"net_liquidating_value": "10000", "used_derivative_buying_power": "4000"}
CFG = RULES["portfolio"]["scenarios"]["realistic"]


def _leg(qty, right, strike, dte=30, iv=0.20):
    return {
        "symbol": f"{right}{strike}",
        "label": f"{qty:+g} {strike}{right}",
        "quantity": qty,
        "right": right,
        "strike": strike,
        "dte": dte,
        "years": dte / 365.0,
        "iv": iv,
        "mid": 1.0,
        "extrinsic": None,
        "priced": True,
        "expiration": f"2026-12-{min(dte, 28):02d}",
    }


def _trade(underlying, spot, legs, tid, kind="credit", downside=None, shape="iron condor", short_dte=None, short_exp=None):
    return {
        "trade_id": tid,
        "underlying": underlying,
        "spot": spot,
        "legs": legs,
        "kind": kind,
        "shape": shape,
        "profile": {"downside_from_now": downside},
        "nearest_short_dte": short_dte,
        "nearest_short_expiry": short_exp,
    }


def _metrics(**betas):
    out = {"SPY": {"last": "500", "beta": "1.0", "corr_spy_3month": "1.0", "iv_index": "0.20"}, "VIX": {"last": "20"}}
    for sym, beta in betas.items():
        out[sym] = {"beta": str(beta), "corr_spy_3month": "0.9"}
    return out


def _history(days=400, step=0.01, k=-3.0):
    """A market that alternates a rise and a fall of `step` a day, VIX moving exp(k x return) with it:
    every 1-day window's VIX ratio is exactly that, so the median is known."""
    rows, spx, vix = [], 1000.0, 20.0
    for i in range(days):
        rows.append([f"2010-{1 + i // 28 % 12:02d}-{1 + i % 28:02d}", spx, vix])
        r = step if i % 2 == 0 else -step
        spx *= 1 + r
        vix *= math.exp(k * r)
    rows.sort(key=lambda x: x[0])
    return {"source": "test", "rows": rows}


def _read(trades, rules=RULES, spot_vol=None, metrics=None):
    return portfolio.reading(trades, metrics or _metrics(), "SPY", BALANCES, None, rules, None, spot_vol)


# --------------------------------------------------------------------------- the history fit
def test_the_curve_is_vix_s_median_answer_to_a_move_that_size():
    # One-day windows: SPX up 1% always came with VIX x exp(-0.03), down 1% with x exp(+0.03).
    cfg = {**CFG, "since": "2000-01-01", "min_window_days": 1}
    hist = {"source": "t", "rows": []}
    spx, vix = 1000.0, 20.0
    for i in range(60):
        hist["rows"].append([f"2010-01-{i:02d}" if i < 10 else f"2010-0{1 + i // 30}-{i % 30:02d}", spx, vix])
        r = 0.01 if i % 2 == 0 else -0.01
        spx, vix = spx * (1 + r), vix * math.exp(-3.0 * r)
    hist["rows"].sort(key=lambda x: x[0])
    curve = spotvol.curve(hist, [-0.01, 0.0, 0.01, 0.10], 1, cfg)
    by = {c["move"]: c for c in curve}
    assert by[0.0]["vix_multiple"] == 1.0
    assert by[0.01]["vix_multiple"] == pytest.approx(math.exp(-0.03), abs=1e-4)
    assert by[-0.01]["vix_multiple"] == pytest.approx(math.exp(0.0301), abs=1e-3)
    # No window moved 10%: no figure, never a guess.
    assert by[0.10]["vix_multiple"] is None and by[0.10]["windows"] == 0


def test_no_history_means_no_curve():
    assert spotvol.curve(None, [0.01], 5, CFG) is None
    assert spotvol.curve({"rows": []}, [0.01], 5, CFG) is None


# --------------------------------------------------------------------------- horizons and the history row
def _condor():
    return _trade(
        "SPY",
        500.0,
        [_leg(-1, "P", 470.0, iv=0.26), _leg(1, "P", 465.0, iv=0.27), _leg(-1, "C", 530.0, iv=0.15), _leg(1, "C", 535.0, iv=0.14)],
        "SPY-1",
        downside=-420.0,
    )


def test_each_horizon_states_the_matrix_with_that_much_time_gone():
    r = _read([_condor()])
    s = r["scenarios"]
    assert [h["horizon_days"] for h in s["horizons"]] == RULES["portfolio"]["scenarios"]["horizons"]
    assert s["horizons"][0]["matrix"] == s["matrix"]
    later = next(h for h in s["horizons"] if h["horizon_days"] == 7)
    flat = s["moves"].index(0.0)
    held = next(row for row in later["matrix"] if row["iv_shift"] == 0.0)
    # A week on with nothing moved, a short condor has earned its decay: the time-held-still cell is 0.
    assert s["matrix"][1]["pnl"][flat] == 0.0 and held["pnl"][flat] > 0.0
    assert held["pnl"][flat] == pytest.approx(
        portfolio.book_pnl(portfolio_rows(r), 500.0, 0.04, 0.0, 0.0, 7 / 365.0, True)[0], abs=0.01
    )


def portfolio_rows(_r):
    # The book the reading priced, rebuilt the same way (one name, its legs).
    c = _condor()
    return [{"underlying": "SPY", "spot": 500.0, "beta": 1.0, "legs": c["legs"]}]


def test_the_history_row_moves_iv_by_vix_s_ratio_from_today_s_vix():
    hist = _history()
    r = _read([_condor()], spot_vol=hist)
    h0 = r["scenarios"]["horizons"][0]
    real = h0["realistic"]
    assert real is not None and r["scenarios"]["realistic"]["error"] is None
    moves = r["scenarios"]["moves"]
    curve = spotvol.curve(hist, moves, h0["window_days"], CFG)
    for j, c in enumerate(curve):
        if c["vix_multiple"] is None:
            assert real["pnl"][j] is None
            continue
        shift = 20.0 / 100.0 * (c["vix_multiple"] - 1.0)
        assert real["iv_shift"][j] == pytest.approx(shift, abs=1e-5)
        expect = portfolio.book_pnl(portfolio_rows(r), 500.0, 0.04, moves[j], shift, 0.0, True)[0]
        assert real["pnl"][j] == pytest.approx(expect, abs=0.01)


def test_without_history_or_vix_there_is_no_history_row_and_it_says_why():
    r = _read([_condor()])
    assert r["scenarios"]["horizons"][0]["realistic"] is None
    assert r["scenarios"]["realistic"]["error"] == "no SPX/VIX history in the snapshot"
    m = _metrics()
    del m["VIX"]
    r = _read([_condor()], spot_vol=_history(), metrics=m)
    assert r["scenarios"]["realistic"]["error"] == "no VIX reading"


# --------------------------------------------------------------------------- ideas
def _book():
    spy = _condor()
    dia = _trade(
        "DIA",
        400.0,
        [_leg(-1, "P", 365.0, iv=0.30), _leg(1, "P", 355.0, iv=0.31), _leg(-1, "C", 420.0, iv=0.18), _leg(1, "C", 430.0, iv=0.17)],
        "DIA-1",
        downside=-700.0,
    )
    return spy, dia


def _ideas(trades, rules=RULES):
    return {i["key"]: i for i in _read(trades, rules=rules, metrics=_metrics(DIA=1.2, AAA=1.4))["ideas"]}


def test_two_broad_indexes_over_the_budget_are_one_bet_and_closing_the_biggest_is_modelled():
    spy, dia = _book()
    ideas = _ideas([spy, dia])
    bucket = ideas["index_bucket"]
    assert bucket["header"] == "DIA and SPY are one bet"
    assert "$1,120 together: 11.2% of net liq" in bucket["subheader"]
    e = bucket["effect"]
    assert e["change"] == "closing the DIA iron condor"
    # The after row is the book without DIA: the SPY condor alone.
    alone = _read([_condor()])
    assert e["after"] == alone["scenarios"]["matrix"][1]["pnl"]
    # Under the budget it does not fire.
    rules = copy.deepcopy(RULES)
    rules["portfolio"]["ideas"]["index_bucket"]["max_pct_nlv"] = 0.2
    assert "index_bucket" not in _ideas([spy, dia], rules)


def test_one_index_alone_is_not_a_bucket():
    spy, _ = _book()
    spy["profile"]["downside_from_now"] = -900.0
    assert "index_bucket" not in _ideas([spy])


def test_a_trade_over_the_cap_is_modelled_at_the_cap():
    spy, _ = _book()
    spy["profile"]["downside_from_now"] = -600.0  # 6% of 10,000, cap 3% = $300: half size
    idea = _ideas([spy])["trade_size"]
    assert idea["header"] == "The SPY iron condor is bigger than one trade should be"
    full = idea["effect"]["before"]
    half = idea["effect"]["after"]
    for b, a in zip(full, half):
        assert a == pytest.approx(b / 2, abs=0.02)
    assert idea["effect"]["theta_after"] == pytest.approx(idea["effect"]["theta_before"] / 2, abs=0.01)


def test_the_cheap_call_side_is_named_and_dropping_it_is_modelled():
    spy, dia = _book()
    idea = _ideas([spy, dia])["call_side"]
    assert idea["subheader"] == "SPY and DIA: calls sold at 15-18% implied vol, puts at 26-30%"
    puts_only = [_trade("SPY", 500.0, [l for l in spy["legs"] if l["right"] == "P"], "x"), _trade("DIA", 400.0, [l for l in dia["legs"] if l["right"] == "P"], "y")]
    expect = _read(puts_only, metrics=_metrics(DIA=1.2))["scenarios"]["matrix"][1]["pnl"]
    assert idea["effect"]["after"] == expect
    # Calls priced near the puts: no idea.
    for l in spy["legs"] + dia["legs"]:
        l["iv"] = 0.25
    assert "call_side" not in _ideas([spy, dia])


def test_a_short_leg_about_to_expire_carrying_the_income_is_named():
    cal = _trade(
        "AAA",
        101.0,
        [_leg(-1, "P", 100.0, dte=6, iv=0.55), _leg(1, "P", 100.0, dte=34, iv=0.45)],
        "AAA-1",
        kind="debit",
        shape="calendar",
        downside=-150.0,
        short_dte=6,
        short_exp="2026-12-04",
    )
    spy, _ = _book()
    idea = _ideas([spy, cal])["short_dated_theta"]
    assert idea["header"] == "Much of the income is AAA's last six days"
    assert "expires 4 December" in idea["subheader"]
    assert idea["effect"]["change"] == "closing the AAA calendar"
    # Further out than `within_days`: no idea.
    cal["nearest_short_dte"] = 30
    assert "short_dated_theta" not in _ideas([spy, cal])


def test_an_expiry_carrying_too_much_is_named_without_a_model():
    spy, dia = _book()
    for t in (spy, dia):
        t["nearest_short_expiry"] = "2026-12-18"
    idea = _ideas([spy, dia])["expiry_cluster"]
    assert idea["header"] == "Two trades expire on 18 December"
    assert idea["effect"] is None


def test_no_net_liq_no_ideas():
    spy, dia = _book()
    r = portfolio.reading([spy, dia], _metrics(DIA=1.2), "SPY", {"net_liquidating_value": None}, None, RULES)
    assert r["ideas"] == []
