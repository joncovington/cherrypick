"""Hypotheses 60-62 (scripts/afternoon_0dte_items60_62.py): the FOMC expected-move flies, the
sandwich read and the midpoint fade. Invented quotes and trails only, no store.

Each bar is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "afternoon_0dte_items60_62.py"


def _module():
    spec = importlib.util.spec_from_file_location("afternoon_0dte_items60_62", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


af = _module()


def flat_chain(spot: float, iv_pts: float = 10.0, half: float = 0.1, lo: int = 4900, hi: int = 5100) -> dict:
    """Five-point strikes, each priced at intrinsic plus a time value that shrinks with distance."""
    ch = {}
    for k in range(lo, hi + 1, 5):
        tv = max(iv_pts - abs(k - spot) * 0.2, 0.05)
        for r, intr in (("C", max(spot - k, 0.0)), ("P", max(k - spot, 0.0))):
            m = intr + tv
            ch[(float(k), r)] = {"bid": round(max(m - half, 0.0), 2), "ask": round(m + half, 2)}
    return ch


def test_working_sits_halfway_between_mid_and_natural_and_q25_a_quarter():
    ch = flat_chain(5000.0)
    legs = af.iron_fly(5000.0, 20)
    m, w, q, n = (af.structure(ch, legs, f) for f in ("mid", "working", "q25", "natural"))
    assert n < w < q < m and abs(w - (m + n) / 2) < 1e-6
    assert af.structure(ch, legs, "working", close=True) > af.structure(ch, legs, "mid", close=True)


def test_a_missing_leg_prices_to_none():
    ch = flat_chain(5000.0)
    del ch[(5020.0, "C")]
    assert af.structure(ch, af.iron_fly(5000.0, 20), "mid") is None


def test_settlement_owes_the_move_inside_the_wings():
    legs = af.iron_fly(5000.0, 20)
    assert af.settle(legs, 5000.0) == (0.0, 0)
    assert af.settle(legs, 5007.0) == (7.0, 1)
    assert af.settle(legs, 4950.0) == (20.0, 2)


def test_the_fomc_flies_centre_on_spot_plus_and_minus_the_straddle():
    ch = flat_chain(5000.0)
    r = af.fomc_trade(ch, 5000.0, {}, None, 5000.0, fill="mid", wing=20, decay=0.10)
    assert r["move"] == 20.0  # straddle mid 10 + 10
    assert r["flies"]["upper"]["centre"] == 5020.0 and r["flies"]["lower"]["centre"] == 4980.0


def test_a_fly_is_bought_back_once_it_has_decayed_ten_percent_else_it_settles():
    ch = flat_chain(5000.0)
    calm = flat_chain(5000.0, iv_pts=3.0)  # the crush: time value falls from 10 to 3 points
    r = af.fomc_trade(ch, 5000.0, {"14:05": "x"}, lambda _: calm, 5000.0, fill="mid", wing=20, decay=0.10)
    assert all(f["exit"] == "14:05" for f in r["flies"].values())
    held = af.fomc_trade(ch, 5000.0, {"14:05": "x"}, lambda _: ch, 5000.0, fill="mid", wing=20, decay=0.10)
    assert all(f["exit"] == "16:00" for f in held["flies"].values())
    assert r["net"] == round(sum(f["net"] for f in r["flies"].values()), 2)


def test_without_a_straddle_the_session_is_skipped():
    assert af.fomc_trade({}, 5000.0, {}, None, 5000.0, fill="mid", wing=20, decay=0.1) == {
        "skip": "no straddle"
    }


def test_the_sandwich_shorts_sit_nearest_spot_plus_and_minus_five():
    legs = af.sandwich_legs(flat_chain(5001.0), 5001.0)
    assert legs == [((5005.0, "C"), -1), ((4995.0, "P"), -1), ((5010.0, "C"), 1), ((4990.0, "P"), 1)]


def test_the_fade_signal_buys_below_the_days_midpoint_and_sells_above():
    trail = {"09:30": 5000.0, "11:00": 5040.0, "13:45": 5010.0, "15:00": 4900.0}
    assert af.fade_signal(trail) == (1, 5010.0, 5020.0)  # midpoint of 5000-5040; the 15:00 low is unseen
    assert af.fade_signal({"09:30": 5000.0, "11:00": 4960.0, "13:45": 4995.0})[0] == -1
    assert af.fade_signal({"09:30": 5000.0}) is None


def _rows(xs: list[float], key="net", start="2022-06-01") -> list[dict]:
    d0 = date.fromisoformat(start)
    return [{"day": (d0 + timedelta(days=i)).isoformat(), key: x, "cost": 10.0} for i, x in enumerate(xs)]


def _judged(xs, *, key="net", min_n=25, costs=True):
    rows = _rows(xs, key)
    return af.judge(rows, rows[0]["day"], rows[-1]["day"], key=key, min_n=min_n, costs=costs)


def test_a_strong_steady_edge_passes():
    assert _judged([60.0, 40.0] * 20)["verdict"] == "pass"


def test_too_few_sessions_is_no_verdict():
    assert _judged([60.0, 40.0] * 10)["verdict"] == "no verdict"


def test_a_noisy_edge_fails_the_t_bar():
    r = _judged([300.0, -290.0] * 20)
    assert r["verdict"] == "fail" and not r["checks"]["t_above_bar"]


def test_a_losing_second_half_fails():
    r = _judged([80.0, 60.0] * 10 + [-5.0, -1.0] * 10)
    assert r["verdict"] == "fail" and not r["checks"]["both_halves_positive"]


def test_an_edge_below_half_its_costs_fails_the_stress():
    r = _judged([6.0, 4.0] * 20)  # mean 5 against costs of 10: 1.5x costs takes 5 off
    assert r["verdict"] == "fail" and not r["checks"]["survives_1_5x_costs"]


def test_the_fade_is_judged_in_points_without_a_cost_check():
    r = _judged([2.0, 1.0] * 60, key="points", min_n=100, costs=False)
    assert r["verdict"] == "pass" and "survives_1_5x_costs" not in r["checks"]


def test_the_fade_holdout_is_refused_without_the_flag(capsys):
    assert af.main(["fade", "--end", "2025-06-01"]) == 2
    assert "allow-holdout" in capsys.readouterr().err
