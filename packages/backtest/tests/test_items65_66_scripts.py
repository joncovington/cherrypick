"""Hypotheses 65 and 66: the intraday premium map's digitals and the earnings straddle's selector,
strike and pricing. Invented quotes only, no store.

Each check is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

from cherrypick.core import bs

ROOT = Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pm = _load("intraday_premium_map_item65")
es = _load("earnings_straddle_item66")


def bs_chain(spot=5000.0, vol=0.15, minutes=120, step=5, half=0.0):
    """Black-Scholes quotes at r = 0 for a 0DTE chain, `minutes` to the close."""
    years = minutes / (390 * 252)
    out = {}
    for k in range(int(spot) - 100, int(spot) + 101, step):
        for r in ("C", "P"):
            m = bs.price(spot, k, years, vol, r, 0.0)
            out[(float(k), r)] = (round(max(m - half, 0.0), 4), round(m + half, 4))
    return out, years


def test_digitals_match_the_model_probability_of_closing_above_each_strike():
    chain, years = bs_chain()
    ds = dict(pm.digitals(chain, 5000.0))
    for k in (4980.0, 5000.0, 5020.0):
        want = bs.prob_itm(5000.0, k, years, 0.15, "C", 0.0)
        assert abs(ds[k] - want) < 0.03, (k, ds[k], want)
    vals = [d for _, d in sorted(ds.items())]
    assert all(a >= b for a, b in zip(vals, vals[1:], strict=False))  # never rises with the strike


def test_the_implied_band_probability_widens_with_the_band_and_shrinks_with_time():
    chain, _ = bs_chain(minutes=120)
    ds = pm.digitals(chain, 5000.0)
    narrow, wide = pm.implied_inside(ds, 5000.0, 0.001), pm.implied_inside(ds, 5000.0, 0.005)
    assert 0 < narrow < wide <= 1
    late, _ = bs_chain(minutes=10)
    assert pm.implied_inside(pm.digitals(late, 5000.0), 5000.0, 0.001) > narrow


def test_a_band_beyond_the_priced_strikes_is_not_read():
    chain, _ = bs_chain()
    ds = pm.digitals(chain, 5000.0)
    assert pm.implied_inside(ds, 5000.0, 0.05) is None


def test_the_gap_is_realised_less_implied_in_points():
    rows = [{"implied": 0.4, "realised": 1.0}, {"implied": 0.4, "realised": 0.0}] * 10
    c = pm.cell(rows)
    assert c["implied_pct"] == 40.0 and c["realised_pct"] == 50.0 and c["gap_pts"] == 10.0
    assert pm.cell([{"implied": 0.5, "realised": 0.5}] * 3)["t"] is None  # no spread, no t


def test_cheap_is_item_47s_selector_turned_over():
    sel = {"short_mean": 0.06, "long_mean": 0.05}
    assert es.cheap(sel, 0.04) and not es.cheap(sel, 0.055) and not es.cheap(sel, 0.07)
    assert not es.cheap(sel, None)


def test_the_straddle_strike_is_nearest_the_close_with_both_sides_quoted():
    chain = {(95.0, "C"): (6.0, 6.2), (95.0, "P"): (1.0, 1.2), (100.0, "C"): (3.0, 3.2),
             (100.0, "P"): (None, 3.1), (105.0, "C"): (1.0, 1.1), (105.0, "P"): (5.9, 6.1)}  # fmt: skip
    assert es.atm_strike(chain, 101.0) == 105.0  # 100's put is one-sided
    assert es.atm_strike({}, 100.0) is None


def test_a_long_straddle_earns_on_a_big_move_and_loses_on_none():
    entry = {(100.0, "C"): (3.0, 3.2), (100.0, "P"): (3.0, 3.2)}
    moved = {(100.0, "C"): (11.8, 12.2), (100.0, "P"): (0.0, 0.1)}
    still = {(100.0, "C"): (1.0, 1.1), (100.0, "P"): (1.0, 1.1)}
    up = es.price_long("XYZ", 100.0, entry, moved, 112.0, 100.0)
    flat = es.price_long("XYZ", 100.0, entry, still, 100.0, 100.0)
    assert up["working"]["net"] > 0 > flat["working"]["net"]
    assert up["stress"]["net"] < up["working"]["net"]
    assert math.isclose(up["working"]["bp"], 1e4 * up["working"]["net"] / 10000.0)


def test_a_traded_straddle_row_scores_through_item_47s_arm_statistics():
    entry = {(100.0, "C"): (3.0, 3.2), (100.0, "P"): (3.0, 3.2)}
    moved = {(100.0, "C"): (11.8, 12.2), (100.0, "P"): (0.05, 0.1)}
    bars = {"e": (100, 100, 100, 100.0), "r": (112, 112, 112, 112.0)}

    class Conn:  # e47.chain reads (strike, right, bid, ask) rows for (symbol, session, expiry)
        def execute(self, _sql, params):
            rows = entry if params[1] == "e" else moved
            return [(k, "CALL" if r == "C" else "PUT", b, a) for (k, r), (b, a) in rows.items()]

    class D(str):
        def isoformat(self):
            return str(self)

    t, why = es.trade(Conn(), "XYZ", (D("e"), D("r"), D("x")), bars)
    assert why is None and t["credit"] == -t["debit"]
    rows = [{**t, "entry_session": f"s{i}", "control_bp": None, "time_half": i % 2, "name_half": i % 2}
            for i in range(4)]  # fmt: skip
    stats = es.e47.arm_stats(rows, bootstrap=False)
    assert stats["trades"] == 4 and stats["mean_credit"] < 0


def test_an_unquoted_exit_leg_is_sold_at_intrinsic_less_its_entry_spread():
    entry = {(100.0, "C"): (3.0, 3.4), (100.0, "P"): (3.0, 3.4)}
    exit_ = {(100.0, "C"): (None, 15.0), (100.0, "P"): (0.05, 0.10)}
    r = es.price_long("XYZ", 100.0, entry, exit_, 110.0, 100.0)
    assert r["exit_unquoted_legs"] == 1
    assert math.isclose(r["exit_value"], 100 * (10.0 - 0.4) + 100 * 0.075)
