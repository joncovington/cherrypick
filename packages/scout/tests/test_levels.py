from cherrypick.scout.analytics import levels


def test_sma_has_none_until_the_window_fills():
    closes = [1, 2, 3, 4, 5]
    out = levels.sma(closes, 3)
    assert out[:2] == [None, None]
    assert out[2] == (1 + 2 + 3) / 3
    assert out[3] == (2 + 3 + 4) / 3
    assert out[4] == (3 + 4 + 5) / 3


def test_sma_matches_a_hand_computed_average():
    closes = [10.0, 20.0, 30.0, 40.0]
    assert levels.sma(closes, 2) == [None, 15.0, 25.0, 35.0]


def test_moving_averages_returns_one_series_per_window():
    bars = [{"c": float(i)} for i in range(1, 250)]
    result = levels.moving_averages(bars)
    assert set(result.keys()) == {"sma20", "sma50", "sma200"}
    assert len(result["sma20"]) == len(bars)


def _bar(high, low, close=None):
    return {"o": high, "h": high, "l": low, "c": close if close is not None else (high + low) / 2, "v": None}


def test_support_resistance_finds_an_obvious_swing_low():
    # A clean V in the lows (and, since h = l + 2 throughout, an inverted-V/no-peak in the highs) --
    # exactly one swing low at index 3, and no spurious swing high anywhere.
    lows = [10, 8, 6, 3, 6, 8, 10]
    bars = [_bar(low + 2, low) for low in lows]
    result = levels.support_resistance(bars, lookback=2)
    assert len(result) == 1
    assert result[0]["kind"] == "support"
    assert result[0]["price"] == 3
    assert result[0]["touches"] == 1


def test_support_resistance_on_flat_data_has_no_swings():
    bars = [_bar(10, 9) for _ in range(10)]
    result = levels.support_resistance(bars, lookback=2)
    assert result == []


def test_support_resistance_clusters_nearby_touches_into_one_level():
    # Two swing lows within tolerance of each other should merge into a single level with 2 touches.
    prices = [100.0, 100.3]
    clustered = levels._cluster(prices, tolerance_pct=0.01)
    assert len(clustered) == 1
    price, touches = clustered[0]
    assert touches == 2
    assert price == (100.0 + 100.3) / 2


def test_support_resistance_keeps_distant_touches_separate():
    clustered = levels._cluster([100.0, 150.0], tolerance_pct=0.01)
    assert len(clustered) == 2
