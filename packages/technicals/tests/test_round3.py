"""Round 3's indicators, setups and runner.

The Vortex and the RSI divergence are restated here from their definitions and checked by hand;
each setup is checked against its rule in the test's own words, both ways (every entry meets it and
every bar that meets it with no position open is an entry), as test_setups.py does for the chart's.
"""

from __future__ import annotations

import math
import sys

from cherrypick.technicals import indicators, round3, setups

sys.path.insert(0, __file__.rsplit("tests", 1)[0] + "tests")
from test_setups import _check, _hand, _readings  # noqa: E402
from test_study_run import _land  # noqa: E402

# ------------------------------------------------------------------------------------- the Vortex


def test_the_vortex_is_summed_movement_over_summed_true_range():
    highs = [10.0, 11.0, 12.5, 12.0, 13.0, 12.0, 14.0]
    lows = [9.0, 9.5, 11.0, 10.5, 11.5, 10.0, 12.0]
    closes = [9.5, 10.5, 12.0, 11.0, 12.5, 11.0, 13.5]
    n = 3
    plus, minus = indicators.vortex(highs, lows, closes, n)
    assert plus[:n] == [None] * n and minus[:n] == [None] * n
    for i in range(n, len(closes)):
        bars = range(i - n + 1, i + 1)
        tr = sum(
            max(highs[k] - lows[k], abs(highs[k] - closes[k - 1]), abs(lows[k] - closes[k - 1])) for k in bars
        )
        assert math.isclose(plus[i], sum(abs(highs[k] - lows[k - 1]) for k in bars) / tr)
        assert math.isclose(minus[i], sum(abs(lows[k] - highs[k - 1]) for k in bars) / tr)


def test_the_vortex_points_up_in_a_rise_and_down_in_a_fall():
    up = [100.0 + i for i in range(40)]
    plus, minus = indicators.vortex([c + 1 for c in up], [c - 1 for c in up], up, 14)
    assert plus[-1] > 1 > minus[-1]
    down = list(reversed(up))
    plus, minus = indicators.vortex([c + 1 for c in down], [c - 1 for c in down], down, 14)
    assert minus[-1] > 1 > plus[-1]


def test_a_flat_market_has_no_vortex_reading():
    flat = [100.0] * 30
    plus, minus = indicators.vortex(flat, flat, flat, 14)
    assert all(v is None for v in plus + minus)


# ---------------------------------------------------------------------------------- the divergence


def _osc(n, bumps):
    """A flat oscillator at 50 (flat bars are never pivots) with a peak or trough at each bump."""
    osc = [50.0] * n
    for at, v in bumps.items():
        osc[at] = v
    return osc


def test_a_lower_rsi_peak_on_a_higher_price_is_bearish_from_its_confirmation_on():
    n, k = 40, 5
    osc = _osc(n, {10: 70.0, 20: 60.0})
    highs = [100.0] * n
    highs[10], highs[20] = 104.0, 106.0
    bear, bull = indicators.divergence(highs, [90.0] * n, osc, k, (5, 60))
    assert not any(bear[: 20 + k]), "the later peak is a pivot only once 5 bars have printed after it"
    assert all(bear[20 + k :]) and not any(bull)
    highs[20] = 103.0  # a lower high on price as well: no divergence
    bear, _ = indicators.divergence(highs, [90.0] * n, osc, k, (5, 60))
    assert not any(bear)


def test_a_higher_rsi_trough_on_a_lower_price_is_bullish():
    n, k = 40, 5
    osc = _osc(n, {10: 30.0, 20: 40.0})
    lows = [100.0] * n
    lows[10], lows[20] = 96.0, 94.0
    bear, bull = indicators.divergence([110.0] * n, lows, osc, k, (5, 60))
    assert all(bull[25:]) and not any(bull[:25]) and not any(bear)


def test_pivots_too_far_apart_are_not_compared():
    n, k = 120, 5
    osc = _osc(n, {10: 70.0, 80: 60.0})  # 70 bars apart, past the 60 allowed
    highs = [100.0] * n
    highs[10], highs[80] = 104.0, 106.0
    bear, _ = indicators.divergence(highs, [90.0] * n, osc, k, (5, 60))
    assert not any(bear)


def test_the_latest_pair_decides_so_a_newer_peak_can_end_a_divergence():
    n, k = 60, 5
    osc = _osc(n, {10: 70.0, 20: 60.0, 32: 65.0})
    highs = [100.0] * n
    highs[10], highs[20], highs[32] = 104.0, 106.0, 107.0
    bear, _ = indicators.divergence(highs, [90.0] * n, osc, k, (5, 60))
    assert all(bear[25:37]) and not any(bear[37:]), "60 -> 65 is a higher RSI peak: no longer diverging"


# -------------------------------------------------------------------------------------- the rules


def _agree(r, i, up):
    s, p, m = r.supertrend_up[i], r.vi_plus[i], r.vi_minus[i]
    if None in (s, p, m):
        return False
    return s and p > m if up else (not s) and p < m


def test_supertrend_vortex_enters_when_the_two_first_agree_and_leaves_when_either_turns():
    for up, sign in ((True, 1), (False, -1)):
        r = _readings(sign=sign)
        setup_id = "st-vortex" if up else "st-vortex-short"
        _check(
            setups.run(setup_id, r),
            lambda i, up=up, r=r: (
                None not in (r.supertrend_up[i - 1], r.vi_plus[i - 1], r.vi_minus[i - 1])
                and _agree(r, i, up)
                and not _agree(r, i - 1, up)
            ),
            lambda j, t, up=up, r=r: (
                r.supertrend_up[j] is (not up)
                or (r.vi_plus[j] < r.vi_minus[j] if up else r.vi_plus[j] > r.vi_minus[j])
            ),
            len(r.closes),
        )


def test_either_indicator_turning_alone_closes_the_position():
    n = 12
    up = [True] * n
    vi_p, vi_m = [1.0] * n, [1.0] * n
    vi_p[1:] = [1.2] * (n - 1)  # bar 1: both agree for the first time -> entry
    vi_p[5] = 0.9  # bar 5: the Vortex turns, Supertrend still up
    r = _hand(n, supertrend_up=up, vi_plus=vi_p, vi_minus=vi_m)
    first = setups.run("st-vortex", r)[0]
    assert (first.entry, first.exit, first.reason) == (1, 5, "vortex")
    up2 = [True] * n
    up2[4] = False  # Supertrend turns, the Vortex still agrees
    r = _hand(n, supertrend_up=up2, vi_plus=[1.0] + [1.2] * (n - 1), vi_minus=vi_m)
    first = setups.run("st-vortex", r)[0]
    assert (first.entry, first.exit, first.reason) == (1, 4, "supertrend")


def test_squeeze_divergence_needs_the_squeeze_the_band_and_the_divergence():
    for up, sign in ((True, 1), (False, -1)):
        r = _readings(sign=sign)
        setup_id = "squeeze-div" if up else "squeeze-div-short"

        def outside(i, up=up, r=r):
            return r.closes[i] > r.bb_upper[i] if up else r.closes[i] < r.bb_lower[i]

        _check(
            setups.run(setup_id, r),
            lambda i, up=up, r=r, outside=outside: (
                outside(i) and any(r.squeeze[i - 4 : i + 1]) and (r.bull_div[i] if up else r.bear_div[i])
            ),
            lambda j, t, up=up, r=r: r.closes[j] < r.bb_mid[j] if up else r.closes[j] > r.bb_mid[j],
            len(r.closes),
        )


def test_the_ablations_drop_exactly_one_condition():
    r = _readings()
    _check(
        setups.run("st-only", r),
        lambda i: r.supertrend_up[i] is True and r.supertrend_up[i - 1] is False,
        lambda j, t: r.supertrend_up[j] is False,
        len(r.closes),
    )
    _check(
        setups.run("vortex-only", r),
        lambda i: r.vi_plus[i - 1] <= r.vi_minus[i - 1] and r.vi_plus[i] > r.vi_minus[i],
        lambda j, t: r.vi_plus[j] < r.vi_minus[j],
        len(r.closes),
    )
    _check(
        setups.run("squeeze-band", r),
        lambda i: r.closes[i] > r.bb_upper[i] and any(r.squeeze[i - 4 : i + 1]),
        lambda j, t: r.closes[j] < r.bb_mid[j],
        len(r.closes),
    )
    _check(
        setups.run("div-band", r),
        lambda i: r.closes[i] > r.bb_upper[i] and r.bull_div[i],
        lambda j, t: r.closes[j] < r.bb_mid[j],
        len(r.closes),
    )


def test_every_entry_fires_in_the_state_its_matched_baseline_is_drawn_from():
    """The matched baseline keeps draws made on days the setup's state held; if a real entry could
    fire outside that state, the comparison would not be like for like."""
    for sign in (1, -1):
        r = _readings(sign=sign)
        for setup_id in round3.FAMILY:
            for t in setups.run(setup_id, r):
                assert round3.MATCHED[setup_id](r, t.entry), (setup_id, t)


def test_the_chart_does_not_draw_the_study_only_setups():
    assert not {s.id for s in setups.STUDIED} & {s.id for s in setups.SETUPS}
    assert not {s.id for s in setups.STUDIED} & set(setups.RUN)


# ------------------------------------------------------------------------------------- the runner


def test_round3_runs_end_to_end_and_judges_nothing_on_a_tiny_sample(tmp_path):
    path = tmp_path / "history.db"
    _land(path, names=6, sessions=700)
    result = round3.run(workers=2, path=path, progress=lambda m: None)
    assert result["names_in_universe"] == 6
    assert set(result["hypotheses"]) == {
        round3.hypothesis_id(s, b) for s in round3.FAMILY for b in round3.BASELINES
    }
    traded = 0
    for setup_id, v in result["setups"].items():
        assert set(v["ablations"]) == set(round3.ABLATIONS[setup_id])
        assert v["verdict"]["confirmed"] is False  # six names never clear the sample threshold
        d = v["describe"]
        if d["entries"]:
            traded += 1
            # every entry keeps its draws (5 other names a day, up to 20 of its own days), and the
            # matched ones are a subset of them
            assert d["median_baseline_draws"] >= 20
            assert d["median_matched_draws"] <= d["median_baseline_draws"]
            if setup_id.startswith("st-vortex"):
                # the two agree on well under every day, so matching must drop draws: a matched sum
                # that took every draw would read equal here
                assert d["median_matched_draws"] < d["median_baseline_draws"]
    assert traded >= 2, "the synthetic history should trade at least the Supertrend + Vortex sides"
    assert not any(v["test"].get("judged") for v in result["hypotheses"].values())
