"""core.indicators: the stochastic and MACD series added for core.signals. The rest is the copy
technicals' own tests have always pinned (they now run against this module through its re-export)."""

from __future__ import annotations

import pytest

from cherrypick.core import indicators as ind


def test_stochastic_matches_the_definition_by_hand():
    highs = [10, 12, 11, 13, 14, 12]
    lows = [8, 9, 9, 10, 11, 10]
    closes = [9, 11, 10, 12, 13, 11]
    k, d = ind.stochastic(highs, lows, closes, 3, 2, 2)
    raw = [None, None]
    for i in range(2, 6):
        hi, lo = max(highs[i - 2 : i + 1]), min(lows[i - 2 : i + 1])
        raw.append(100 * (closes[i] - lo) / (hi - lo))
    want_k = [None, None, None] + [(raw[i - 1] + raw[i]) / 2 for i in range(3, 6)]
    assert k == pytest.approx(want_k)
    assert d[:4] == [None] * 4
    assert d[4:] == pytest.approx([(want_k[3] + want_k[4]) / 2, (want_k[4] + want_k[5]) / 2])


def test_a_flat_window_reads_fifty():
    k, _ = ind.stochastic([5] * 4, [5] * 4, [5] * 4, 3, 1, 1)
    assert k == [None, None, 50.0, 50.0]


def test_macd_is_the_ema_difference_and_its_signal():
    closes = [float(x) for x in [10, 11, 12, 11, 13, 14, 13, 15, 16, 15, 17]]
    line, sig, hist = ind.macd(closes, 3, 5, 3)
    f, s = ind.ema(closes, 3), ind.ema(closes, 5)
    assert line[:4] == [None] * 4
    assert line[4:] == pytest.approx([f[i] - s[i] for i in range(4, 11)])
    assert sig[:6] == [None] * 6
    assert sig[6:] == pytest.approx(ind.ema(line[4:], 3)[2:])
    assert hist[6:] == pytest.approx([line[i] - sig[i] for i in range(6, 11)])


def test_a_gap_inside_a_series_is_refused():
    with pytest.raises(ValueError):
        ind._on_tail(ind.sma, [None, 1.0, None, 2.0], 2)
