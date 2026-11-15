"""core.chainmeasures on a synthetic chain priced by core.bs from a known smile, so every measure has an
answer known in advance."""

from __future__ import annotations

import math

import pytest

from cherrypick.core import bs
from cherrypick.core import chainmeasures as cm

F, YEARS = 5000.0, 30 / 365


def smile(k, base=0.18, slope=0.6):
    """Put-side skew: vol rises as the strike falls below the forward."""
    return base + slope * (F - k) / F


def chain(vol=smile, half_spread=0.0, strikes=range(4500, 5501, 25)):
    rows = []
    for k in strikes:
        v = vol(k)
        for right in ("C", "P"):
            p = bs.price(F, k, YEARS, v, right, 0.0)
            rows.append(
                {
                    "strike": float(k),
                    "right": right,
                    "bid": max(p - half_spread, 0.0),
                    "ask": p + half_spread,
                    "iv": v,
                    "delta": bs.delta(F, k, YEARS, v, right, 0.0),
                }
            )
    return rows


# --- realized vol, rank, percentile ------------------------------------------------------------------


def test_realized_vol_is_the_sample_stdev_of_log_returns_annualized():
    closes = [100, 101, 99.5, 100.5, 102, 101]
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:], strict=False)]
    mean = sum(rets) / len(rets)
    want = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1) * 252) * 100
    assert cm.realized_vol(closes) == pytest.approx(want)
    assert cm.realized_vol([100, 101]) is None
    assert cm.realized_vol([100, 0, 101]) is None


def test_iv_rank_and_percentile():
    hist = [12.0, 15.0, 20.0, 30.0]
    assert cm.iv_rank(21.0, hist) == pytest.approx(100 * 9 / 18)
    assert cm.iv_rank(40.0, hist) == 100.0  # a new high ranks at the top of its own window
    assert cm.iv_rank(15.0, [15.0, 15.0]) == 50.0
    assert cm.iv_percentile(21.0, hist) == 75.0
    assert cm.iv_percentile(12.0, hist) == 0.0  # strictly below
    assert cm.iv_rank(1.0, []) is None and cm.iv_percentile(1.0, []) is None


# --- chain measures -----------------------------------------------------------------------------------


@pytest.mark.parametrize("iv_from", ["row", "mid"])
def test_atm_iv_recovers_the_vol_at_the_forward(iv_from):
    assert cm.atm_iv(chain(), years=YEARS, iv_from=iv_from) == pytest.approx(18.0, abs=0.01)


@pytest.mark.parametrize(("iv_from", "delta_from"), [("row", "row"), ("mid", "mid"), ("row", "mid")])
def test_skew_is_positive_on_a_put_skew_and_zero_on_a_flat_smile(iv_from, delta_from):
    skew = cm.skew_25d(chain(), years=YEARS, iv_from=iv_from, delta_from=delta_from)
    assert skew is not None and skew > 1.0
    flat = cm.skew_25d(chain(vol=lambda k: 0.2), years=YEARS, iv_from=iv_from, delta_from=delta_from)
    assert flat == pytest.approx(0.0, abs=0.01)


def test_delta_iv_picks_the_strike_nearest_the_delta():
    rows = chain()
    puts = [r for r in rows if r["right"] == "P"]
    nearest = min(puts, key=lambda r: (abs(abs(r["delta"]) - 0.25), r["strike"]))
    got = cm.delta_iv(rows, "P", 0.25, years=YEARS, iv_from="row", delta_from="row")
    assert got == pytest.approx(nearest["iv"] * 100)


def test_a_row_missing_the_chosen_source_is_skipped_never_filled_from_the_other():
    rows = chain()
    for r in rows:
        r["iv"] = None  # the feed sent no IV
    assert cm.atm_iv(rows, years=YEARS, iv_from="row") is None
    assert cm.atm_iv(rows, years=YEARS, iv_from="mid") == pytest.approx(18.0, abs=0.01)


def test_iv_from_mid_ignores_the_rows_own_iv():
    rows = chain()
    for r in rows:
        r["iv"] = 0.99  # a feed value that disagrees with every mid
    assert cm.atm_iv(rows, years=YEARS, iv_from="mid") == pytest.approx(18.0, abs=0.01)
    assert cm.atm_iv(rows, years=YEARS, iv_from="row") == pytest.approx(99.0)


def test_a_source_must_be_named():
    with pytest.raises(ValueError, match="iv_from"):
        cm.atm_iv(chain(), years=YEARS, iv_from="best")
    with pytest.raises(ValueError, match="delta_from"):
        cm.skew_25d(chain(), years=YEARS, iv_from="row", delta_from="feed")


def test_a_chain_without_a_parity_pair_has_no_atm_iv():
    calls = [r for r in chain() if r["right"] == "C"]
    assert cm.atm_iv(calls, years=YEARS, iv_from="row") is None


def test_iv_minus_rv_and_the_term_readings():
    closes = [100, 101, 99.5, 100.5, 102, 101]
    assert cm.iv_minus_rv(20.0, closes) == pytest.approx(20.0 - cm.realized_vol(closes))
    assert cm.iv_minus_rv(None, closes) is None
    assert cm.term_spread(16.0, 19.0) == 3.0
    assert cm.term_ratio(16.0, 20.0) == 0.8
    assert cm.term_ratio(16.0, 0.0) is None


# --- net greeks ---------------------------------------------------------------------------------------


def test_net_greeks_sum_signed_legs_times_the_multiplier():
    legs = [
        {"quantity": -1, "delta": -0.10, "gamma": 0.002, "theta": -1.5, "vega": 3.0},
        {"quantity": 1, "delta": -0.05, "gamma": 0.001, "theta": -0.8, "vega": 2.0},
    ]
    got = cm.net_greeks(legs, multiplier=100)
    assert got["delta"] == pytest.approx(5.0)
    assert got["gamma"] == pytest.approx(-0.1)
    assert got["theta"] == pytest.approx(70.0)
    assert got["vega"] == pytest.approx(-100.0)


def test_a_greek_any_leg_lacks_is_none_for_the_position():
    legs = [{"quantity": -1, "delta": -0.1, "vega": 3.0}, {"quantity": 1, "delta": -0.05, "vega": None}]
    got = cm.net_greeks(legs, multiplier=100)
    assert got["delta"] == pytest.approx(5.0)
    assert got["vega"] is None and got["gamma"] is None
    assert cm.net_greeks([], multiplier=100)["delta"] is None


def test_every_measure_declares_what_it_reads():
    public = {n for n in dir(cm) if not n.startswith("_") and callable(getattr(cm, n))}
    measures = public - {"Any", "Iterable", "Mapping", "Sequence"}
    assert measures == set(cm.NEEDS)
