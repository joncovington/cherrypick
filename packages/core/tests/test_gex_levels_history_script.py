"""Item 12's GEX study (scripts/gex_levels_history.py): its levels and its three declared tests, on
synthetic sessions only. Each pass test is shown to fire on a built-in effect and to fail without one,
because a check that cannot fire reads as coverage it is not."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "gex_levels_history.py"


def _module():
    spec = importlib.util.spec_from_file_location("gex_levels_history", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = _module()


def _rows():
    # Call OI piles at 105, put OI at 95; gamma on both, so the surface builds.
    rows = []
    for k in range(90, 111):
        rows.append(
            {
                "strike": float(k),
                "call_oi": 5000.0 if k == 105 else 100.0,
                "put_oi": 5000.0 if k == 95 else 100.0,
                "call_gamma": 0.01,
                "put_gamma": 0.01,
            }
        )
    return rows


def test_levels_name_the_oi_walls_and_pins():
    lv = m.levels(_rows(), 100.0)
    assert lv["oi"]["call_wall"] == 105.0 and lv["oi"]["put_wall"] == 95.0
    assert lv["oi"]["pin_above"] == 105.0 and lv["oi"]["pin_below"] == 95.0
    assert lv["net_gex"] is not None and "gamma" in lv


def _day(date, hit):
    return {"date": date, "pin": {"600:oi:above": hit, "600:oi:below": hit}, "barrier": None, "regime": None}


def test_the_pin_test_fires_on_a_real_pin_and_not_on_a_coin():
    strong = [_day(f"d{i:03d}", i % 10 != 0) for i in range(200)]  # 90% hits
    coin = [_day(f"d{i:03d}", i % 2 == 0) for i in range(200)]
    ok = m.verdict({"a": {"pin": m.pin_test(strong), "barrier": {}, "regime": {"ci": None}},
                    "b": {"pin": m.pin_test(strong), "barrier": {}, "regime": {"ci": None}}})  # fmt: skip
    assert ok["pin"]["pass"] is True
    no = m.verdict({"a": {"pin": m.pin_test(coin), "barrier": {}, "regime": {"ci": None}},
                    "b": {"pin": m.pin_test(strong), "barrier": {}, "regime": {"ci": None}}})  # fmt: skip
    assert no["pin"]["pass"] is False  # one half only is not a pass


def _bday(i, touched, reach=1.0):
    return {
        "date": f"d{i:03d}",
        "pin": {},
        "up_reach": reach,
        "down_reach": reach,
        "barrier": {
            "oi:call": {"dist": 0.5, "touched": touched},
            "oi:put": {"dist": 0.5, "touched": touched},
        },
        "regime": None,
    }


def test_the_barrier_test_fires_when_walls_repel():
    # Every day reaches 1.0 straddles, so a wall at 0.5 is expected to be touched every day.
    repel = [_bday(i, i % 4 == 0) for i in range(200)]  # touched a quarter as often as expected
    free = [_bday(i, True) for i in range(200)]
    half = lambda d: {"pin": {}, "barrier": m.barrier_test(d), "regime": {"ci": None}}  # noqa: E731
    assert m.verdict({"a": half(repel), "b": half(repel)})["barrier"]["checks"]["oi"] is True
    assert m.verdict({"a": half(free), "b": half(repel)})["barrier"]["checks"]["oi"] is False


def test_the_regime_test_fires_when_negative_gex_days_range_wider():
    wide = [
        {"date": f"d{i}", "regime": {"negative": i % 2 == 0, "range": 2.0 if i % 2 == 0 else 1.0}}
        for i in range(100)
    ]
    flat = [
        {"date": f"d{i}", "regime": {"negative": i % 2 == 0, "range": 1.0 + (i % 3) * 0.1}}
        for i in range(100)
    ]
    half = lambda d: {"pin": {}, "barrier": {}, "regime": m.regime_test(d)}  # noqa: E731
    assert m.verdict({"a": half(wide), "b": half(wide)})["regime"]["pass"] is True
    assert m.verdict({"a": half(flat), "b": half(wide)})["regime"]["pass"] is False


def test_the_holdout_is_refused_unless_allowed(tmp_path):
    assert m.touches_holdout("2026-01-01", "2026-12-31")
    assert not m.touches_holdout(None, None)  # the declared halves end before it
    assert (
        m.main(["--start", "2026-01-01", "--end", "2026-12-31", "--history-db", str(tmp_path / "x.db")]) == 2
    )


def test_a_window_splits_into_its_own_halves():
    days = [{"date": f"2026-{i:02d}-01"} for i in range(1, 11)]
    h = m.split_halves(days, "2026-01-01", "2026-12-31")
    assert [len(v) for v in h.values()] == [5, 5]
