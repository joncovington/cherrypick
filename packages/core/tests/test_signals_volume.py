"""VWAP, its bands and the volume sources, against values worked by hand."""

from __future__ import annotations

import pytest

from cherrypick.core import signals as sg


def bar(session, start, close, volume, *, size=5, high=None, low=None, open_=None):
    return sg.Bar(
        session,
        start,
        start + size,
        close if open_ is None else open_,
        close if high is None else high,
        close if low is None else low,
        close,
        volume,
    )


def vwap(anchor="session", price="close", weight="volume"):
    return {"name": "vwap", "anchor": anchor, "price": price, "weight": weight}


DAY1, DAY2, DAY3 = "2026-11-16", "2026-11-17", "2026-11-18"


def test_session_vwap_weights_each_price_by_its_volume_and_starts_over_each_session():
    bars = [bar(DAY1, 570, 100.0, 1), bar(DAY1, 575, 110.0, 3), bar(DAY2, 570, 50.0, 2)]
    assert sg.source(vwap(), bars, seed=True) == [100.0, 107.5, 50.0]
    assert sg.source(vwap(weight="equal"), bars, seed=True) == [100.0, 105.0, 50.0]


def test_the_price_is_the_close_or_a_typical_price():
    b = [bar(DAY1, 570, 100.0, 1, high=103.0, low=94.0, open_=99.0)]
    assert sg.source(vwap(price="hlc3"), b, seed=True) == [pytest.approx(99.0)]
    assert sg.source(vwap(price="ohlc4"), b, seed=True) == [pytest.approx(99.0)]
    assert sg.source(vwap(price="close"), b, seed=True) == [100.0]


def test_a_time_anchor_begins_at_its_bar_each_session_and_is_undefined_before():
    bars = [bar(DAY1, 570, 100.0, 1), bar(DAY1, 600, 110.0, 1), bar(DAY1, 605, 120.0, 1)]
    assert sg.source(vwap(anchor="10:00"), bars, seed=True) == [None, 110.0, 115.0]


def test_an_event_anchor_carries_across_sessions_until_the_next_event():
    bars = [bar(d, 570, c, 1) for d, c in ((DAY1, 10.0), (DAY2, 20.0), (DAY3, 40.0))]
    out = sg.source(vwap(anchor={"event": "fomc"}), bars, seed=False, events={"fomc": [DAY2]})
    assert out == [None, 20.0, 30.0]
    again = sg.source(vwap(anchor={"event": "fomc"}), bars, seed=False, events={"fomc": [DAY1, DAY3]})
    assert again == [10.0, 15.0, 40.0]
    with pytest.raises(ValueError, match="no dates supplied for event 'fomc'"):
        sg.source(vwap(anchor={"event": "fomc"}), bars, seed=False)


def test_a_band_is_k_weighted_standard_deviations_about_the_vwap():
    bars = [bar(DAY1, 570, 100.0, 1), bar(DAY1, 575, 110.0, 1)]  # mean 105, sd 5
    up = {**vwap(), "name": "vwap_band", "k": 2, "side": "upper"}
    lo = {**up, "side": "lower"}
    assert sg.source(up, bars, seed=True) == [100.0, pytest.approx(115.0)]
    assert sg.source(lo, bars, seed=True) == [100.0, pytest.approx(95.0)]


def test_relative_volume_compares_the_same_minute_over_the_sessions_before():
    bars = [
        bar(DAY1, 570, 1.0, 100), bar(DAY1, 575, 1.0, 10),
        bar(DAY2, 570, 1.0, 300), bar(DAY2, 575, 1.0, 30),
        bar(DAY3, 570, 1.0, 400), bar(DAY3, 575, 1.0, 5),
    ]  # fmt: skip
    one = sg.source({"name": "relative_volume", "n": 1}, bars, seed=False)
    assert one == [None, None, 3.0, 3.0, pytest.approx(4 / 3), pytest.approx(5 / 30)]
    two = sg.source({"name": "relative_volume", "n": 2}, bars, seed=False)
    assert two == [None, None, None, None, 2.0, pytest.approx(0.25)]


def test_a_volume_surge_is_a_cross_of_the_ratio_to_the_bars_before():
    bars = [bar(DAY1, 570 + 5 * i, 1.0, v) for i, v in enumerate([10, 10, 10, 10, 40])]
    assert sg.source({"name": "volume_ratio", "n": 3}, bars, seed=False) == [None, None, None, 1.0, 4.0]
    spec = {
        "kind": "cross",
        "a": {"name": "volume_ratio", "n": 3},
        "b": 3,
        "direction": "above",
        "seed": True,
    }
    # Armed by the 1.0, fired by the 4.0 (the bar 09:50-09:55).
    assert [f.minute for f in sg.firings(spec, bars)] == [595]


def test_a_cross_of_price_over_vwap_fires_where_the_close_crosses():
    closes = [100.0, 99.0, 98.0, 103.0, 104.0]
    bars = [bar(DAY1, 570 + 5 * i, c, 1) for i, c in enumerate(closes)]
    spec = {"kind": "cross", "a": {"name": "close"}, "b": vwap(), "direction": "above", "seed": False}
    # VWAP: 100, 99.5, 99, 100, 100.8; the close is under it until 103 > 100.
    assert [f.minute for f in sg.firings(spec, bars)] == [590]


def test_a_volume_source_refuses_bars_without_volume_and_equal_weight_does_not():
    bars = [bar(DAY1, 570, 100.0, None), bar(DAY1, 575, 101.0, 5)]
    for src in (vwap(), {"name": "relative_volume", "n": 1}, {"name": "volume_ratio", "n": 1}):
        with pytest.raises(sg.MissingVolume):
            sg.source(src, bars, seed=True)
    assert sg.source(vwap(weight="equal"), bars, seed=True) == [100.0, 100.5]
    spec = {"kind": "cross", "a": {"name": "close"}, "b": vwap(), "direction": "above", "seed": False}
    assert sg.needs_volume(spec)
    assert not sg.needs_volume({**spec, "b": vwap(weight="equal")})
    assert sg.needs_volume({**spec, "b": 100.0, "only_when": [spec]})


def test_bars_carry_volume_from_trail_ticks_and_daily_rows():
    ticks = [(570, 100.0, 5), (571, 101.0, 7), (575, 102.0, 1), (576, 103.0)]
    bars = sg.from_trail(DAY1, ticks, 5)
    assert [b.volume for b in bars] == [12.0, None], "a tick without volume leaves its bar without"
    assert [b.volume for b in sg.from_trail(DAY1, [(570, 1.0), (571, 2.0)], 5)] == [None]
    rows = [{"session": DAY1, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 900}]
    assert sg.daily(rows)[0].volume == 900.0
    assert sg.daily([{k: v for k, v in rows[0].items() if k != "volume"}])[0].volume is None


BAND = {"name": "vwap_band", "anchor": "session", "price": "close", "weight": "volume"}


@pytest.mark.parametrize(
    ("src", "message"),
    [
        ({"name": "vwap", "anchor": "noon", "price": "close", "weight": "volume"}, "anchor"),
        ({"name": "vwap", "anchor": {"event": ""}, "price": "close", "weight": "volume"}, "anchor"),
        ({"name": "vwap", "anchor": "session", "price": "mid", "weight": "volume"}, "price"),
        ({"name": "vwap", "anchor": "session", "price": "close", "weight": "time"}, "weight"),
        ({"name": "vwap", "anchor": "session", "price": "close"}, "needs 'weight'"),
        ({**BAND, "k": 0, "side": "upper"}, "k"),
        ({**BAND, "k": 2, "side": "mid"}, "side"),
        ({"name": "relative_volume", "n": 0}, "n"),
        ({"name": "volume_ratio"}, "needs 'n'"),
    ],
)  # fmt: skip
def test_a_bad_volume_source_is_refused_by_path(src, message):
    spec = {"kind": "cross", "a": {"name": "close"}, "b": src, "direction": "above", "seed": False}
    with pytest.raises(ValueError, match=message):
        sg.validate(spec)
