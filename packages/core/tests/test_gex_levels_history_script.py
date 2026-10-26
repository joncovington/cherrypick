"""Item 12's GEX study (scripts/gex_levels_history.py): its levels and its three declared tests, on
synthetic sessions only. The barrier and regime tests are shown to fire on a built-in effect and to
fail without one, because a check that cannot fire reads as coverage it is not. The pin test, as
declared, cannot fire on any input a session can produce, and its test shows that instead."""

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


def test_the_gamma_put_wall_is_cores_largest_put_exposure_below_spot():
    # put_gex is positive in core.gex and its put wall is the max: the pile at 95, not the thinnest
    # strike below spot (the old min named 90).
    lv = m.levels(_rows(), 100.0)
    assert lv["gamma"]["put_wall"] == 95.0 and lv["gamma"]["call_wall"] == 105.0
    below = [r for r in _rows() if r["strike"] < 100.0]
    assert lv["gamma"]["put_wall"] == m.profile_surface(below, 100.0)["put_wall"]


def test_the_gamma_put_wall_stays_below_spot():
    # The chain's largest put pile overall is above spot; the wall the barrier reads is below it.
    rows = _rows()
    for r in rows:
        r["put_oi"] = {103.0: 9000.0, 97.0: 3000.0}.get(r["strike"], 100.0)
    assert m.profile_surface(rows, 100.0)["put_wall"] == 103.0
    assert m.levels(rows, 100.0)["gamma"]["put_wall"] == 97.0


def test_the_snapshot_is_the_latest_at_or_before_the_minute():
    a, b, c = [{"id": "a"}], [{"id": "b"}], [{"id": "c"}]
    assert m.snapshot_at([(590, a), (598, b), (605, c)], 600) is b  # never the later one
    assert m.snapshot_at([(600, a), (605, c)], 600) is a
    assert m.snapshot_at([(605, c)], 600) is None  # only a later profile: none
    assert m.snapshot_at([(580, a)], 600) is None  # older than the staleness bound


def _session(date, close, spot=100.4):
    path = [(minute, spot) for minute in range(570, 960)]
    return {"date": date, "close": close, "em": 2.0, "path": path}


def _profiles():
    return [(minute, _rows()) for minute in m.PIN_TIMES]


def test_the_declared_pin_test_cannot_fire_on_any_reachable_day():
    # The strike above is above spot and the one below is below it, so "toward the strike above" is
    # "the close is above spot" and "toward the one below" is its complement. Even a perfect pin,
    # every close landing on the strike above, passes one side and fails the other: the declared
    # test (both sides above 50%) cannot pass, so its fail says nothing about pinning.
    pinned = [m.day_record(_session(f"d{i:03d}", 105.0), _profiles()) for i in range(200)]
    for d in pinned:
        for minute in m.PIN_TIMES:
            for lt in m.LEVELS:
                above, below = d["pin"][f"{minute}:{lt}:above"], d["pin"][f"{minute}:{lt}:below"]
                assert above is not below
    half = {"pin": m.pin_test(pinned), "barrier": {}, "regime": {"ci": None}}
    assert half["pin"]["600:gamma:above"]["lower"] > 0.5
    assert m.verdict({"a": half, "b": half})["pin"]["pass"] is False


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


def test_an_open_end_reaches_the_holdout(tmp_path):
    # `--start` inside the design window or the holdout with no `--end` reads to the store's last
    # session, so it reads the holdout and is refused without --allow-holdout.
    assert m.touches_holdout("2025-08-01", None)
    assert m.touches_holdout("2024-01-01", None)
    assert m.main(["--start", "2025-08-01", "--history-db", str(tmp_path / "x.db")]) == 2
    assert not m.touches_holdout("2026-10-13", None)  # fresh sessions only
    assert not m.touches_holdout(None, "2025-06-30")  # an open start ends before it


def test_a_window_splits_into_its_own_halves():
    days = [{"date": f"2026-{i:02d}-01"} for i in range(1, 11)]
    h = m.split_halves(days, "2026-01-01", "2026-12-31")
    assert [len(v) for v in h.values()] == [5, 5]
