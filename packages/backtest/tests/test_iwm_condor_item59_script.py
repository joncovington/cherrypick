"""Hypothesis 59's study (scripts/iwm_condor_item59.py): the IWM condor's expiry and entry choice,
forward, strikes, pricing, exits and verdict. Invented quotes only (Black-Scholes prices around a
made-up forward), no store.

Each bar is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import bs

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "iwm_condor_item59.py"


def _module():
    spec = importlib.util.spec_from_file_location("iwm_condor_item59", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ic = _module()

EXPIRY = "2024-03-15"


def rows(forward: float, days: int, vol: float = 0.22, half_spread: float = 0.02, scale: float = 1.0) -> list:
    """A chain of whole-dollar strikes, each quoted mid +/- half_spread at `scale` times its model price."""
    years = days / 365.0
    out = []
    for k in range(int(forward) - 40, int(forward) + 41):
        for r, name in (("C", "CALL"), ("P", "PUT")):
            m = bs.price(forward, k, years, vol, r, 0.0) * scale
            out.append((float(k), name, round(max(m - half_spread, 0.0), 2), round(m + half_spread, 2)))
    return out


def chain(forward=200.0, days=45, session="2024-01-30", **kw):
    return ic.Chain(rows(forward, days, **kw), session, EXPIRY)


def test_monthlies_keep_the_third_friday_or_the_holiday_thursday():
    exps = ["2024-03-08", "2024-03-15", "2024-03-22", "2022-04-14", "2024-04-19", "2024-04-18"]
    assert ic.monthlies(exps) == ["2022-04-14", "2024-03-15", "2024-04-19"]


def test_the_entry_is_the_session_nearest_45_days_inside_the_band():
    sessions = ["2024-01-29", "2024-01-30", "2024-01-31", "2024-02-05"]
    assert ic.entry_session(sessions, EXPIRY) == "2024-01-30"  # 45 days
    assert ic.entry_session(["2024-01-26", "2024-02-05"], EXPIRY) is None  # 49 and 39 days


def test_the_forward_is_parity_at_the_strikes_nearest_the_money():
    assert abs(chain(forward=203.4).forward - 203.4) < 0.05
    assert ic.parity_forward({}, {}) is None


def test_the_condor_shorts_sit_near_16_delta_and_the_longs_further_out_near_5():
    c = chain()
    legs = ic.pick_condor(c)
    (sp, _), (sc, _), (lp, _), (lc, _) = legs
    assert sp[1] == "P" and sc[1] == "C" and lp[0] < sp[0] < c.forward < sc[0] < lc[0]
    assert abs(abs(c.delta(c.by["P"][sp[0]])) - 0.16) < 0.03
    assert abs(abs(c.delta(c.by["C"][lc[0]])) - 0.05) < 0.02
    assert [n for _, n in legs] == [-1, -1, 1, 1]


def test_a_sale_brings_less_at_working_than_mid_and_a_buyback_costs_more():
    c = chain()
    legs = ic.pick_condor(c)
    assert ic.price(c, legs, "natural") < ic.price(c, legs, "working") < ic.price(c, legs, "mid")
    assert ic.price(c, legs, "natural", close=True) > ic.price(c, legs, "mid", close=True)


def test_a_missing_strike_prices_to_none():
    c = chain()
    legs = ic.pick_condor(c)
    del c.by["C"][legs[3][0][0]]
    assert ic.price(c, legs, "working") is None


def _path(prices: list[tuple[str, float]]) -> list:
    """Later sessions whose quotes are the entry chain's model prices times `scale`."""
    return [(s, ic.Chain(rows(200.0, ic.days_between(s, EXPIRY), scale=x), s, EXPIRY)) for s, x in prices]


def test_the_condor_closes_at_half_its_credit_on_the_first_session_that_allows():
    entry = chain()
    path = _path([("2024-02-02", 1.0), ("2024-02-09", 0.3), ("2024-02-12", 0.2)])
    r = ic.run_trade(entry, path, EXPIRY, fill="mid")
    assert r["how"] == "target" and r["exit"] == "2024-02-09" and r["net"] > 0


def test_otherwise_it_closes_at_21_days_whatever_the_mark():
    entry = chain()
    # At 21 days the condor has decayed; four times its model price is still a loss on the credit.
    path = _path([("2024-02-02", 1.0), ("2024-02-23", 4.0), ("2024-03-01", 0.1)])
    r = ic.run_trade(entry, path, EXPIRY, fill="mid")
    assert r["how"] == "time" and r["exit"] == "2024-02-23" and r["gross"] < 0


def test_a_condor_with_no_priced_exit_is_skipped():
    entry = chain()
    assert ic.run_trade(entry, [], EXPIRY, fill="mid") == {"skip": "no exit mark"}
    assert ic.run_trade(ic.Chain([], "2024-01-30", EXPIRY), [], EXPIRY, fill="mid") == {
        "skip": "no condor strikes"
    }


def test_costs_are_an_open_and_a_close_fee_and_net_is_gross_less_cost():
    entry = chain()
    r = ic.run_trade(entry, _path([("2024-02-09", 0.3)]), EXPIRY, fill="working")
    assert r["cost"] > 0 and r["net"] == round(r["gross"] - r["cost"], 2)
    assert set(r["sides_gross"]) == {"P", "C"}


def _rows(nets: list[float], start="2016-01-15") -> list[dict]:
    d0 = date.fromisoformat(start)
    return [
        {"entry": (d0 + timedelta(days=30 * i)).isoformat(), "credit": 1.0, "gross": n + 8.0, "net": n,
         "cost": 8.0, "risk": 400.0}
        for i, n in enumerate(nets)
    ]  # fmt: skip


def _judged(nets, skips=None):
    rs = _rows(nets)
    test = ic.score(rs, Counter(skips or {}), rs[0]["entry"], rs[-1]["entry"])
    ic.judge(test)
    return test


def test_a_strong_steady_edge_passes():
    assert _judged([60.0, 40.0] * 35)["verdict"] == "pass"


def test_fewer_than_50_trades_is_no_verdict():
    assert _judged([60.0, 40.0] * 20)["verdict"] == "no verdict"


def test_too_many_entries_without_an_exit_mark_is_no_verdict():
    r = _judged([60.0, 40.0] * 35, {"no exit mark": 5})
    assert r["verdict"] == "no verdict" and "exit mark" in r["why"]
    assert _judged([60.0, 40.0] * 35, {"no exit mark": 3})["verdict"] == "pass"


def test_a_noisy_edge_fails_the_t_bar():
    r = _judged([300.0, -290.0] * 35)
    assert r["verdict"] == "fail" and not r["checks"]["t_above_bar"]


def test_a_losing_second_half_fails():
    r = _judged([80.0, 60.0] * 20 + [-5.0, -1.0] * 20)
    assert r["verdict"] == "fail" and not r["checks"]["both_halves_positive"]


def test_an_edge_smaller_than_half_its_costs_fails_the_stress():
    r = _judged([5.0, 3.0] * 35)  # mean 4 against costs of 8: 1.5x costs takes 4 off
    assert r["verdict"] == "fail" and not r["checks"]["survives_1_5x_costs"]


def test_the_holdout_is_refused_without_the_flag(capsys):
    assert ic.main(["--end", "2022-03-01"]) == 2
    assert "holdout" in capsys.readouterr().err
