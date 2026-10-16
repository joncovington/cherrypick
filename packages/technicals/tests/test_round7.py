"""Round 7's pure parts: the bearing from the frozen table, the entry rule, and the random draws."""

from __future__ import annotations

from cherrypick.technicals import bearing_table, compass, round7


def test_the_frozen_table_gives_the_strong_states_where_the_list_had_them():
    assert round7.bearings([4], [4], [9]) == ["strong bull"]
    assert round7.bearings([-3], [-3], [2]) == ["strong bear"]
    assert round7.bearings([4], [4], [None]) == [None], "no score, no bearing"
    assert round7.bearings([None], [4], [9]) == [None]


def test_the_table_is_what_compass_fits_and_never_names_an_unknown_label():
    assert set(bearing_table.TABLE.values()) <= set(compass.LABELS.values())
    assert all(t in (0, 1, 2) for _, _, t in bearing_table.TABLE)


def test_entering_needs_the_state_today_and_on_none_of_the_fresh_sessions_before():
    labels = ["bull", "strong bull", "strong bull", "pullback"] + ["bull"] * 10 + ["strong bull"]
    assert round7.enters(labels, 1, "strong bull")
    assert not round7.enters(labels, 2, "strong bull"), "already in it the session before"
    assert round7.enters(labels, 14, "strong bull"), "eleven sessions since it was last strong bull"
    assert not round7.enters(labels, 3, "strong bull")


def test_each_test_trades_its_own_side():
    assert round7.setup_of("strong-bull") == "bearing" and round7.setup_of("pullback") == "bearing"
    assert round7.setup_of("strong-bear") == "bearing-short" and round7.setup_of("rally") == "bearing-short"


def test_draws_come_from_the_day_and_the_name_and_replay_exactly():
    entries = [{"test": "strong-bull", "symbol": "A", "date": "d1"}]
    by_date = {"d1": ["A"] + [f"S{i}" for i in range(60)]}
    by_name = {"A": ["d1"] + [f"x{i}" for i in range(60)]}
    work = round7.draw_plan(entries, by_date, by_name)
    others = [s for s, items in work.items() if s != "A" for *_, is_draw in items if is_draw]
    own = [d for _, d, _, is_draw in work["A"] if is_draw]
    assert len(others) == round7.DRAWS_SAME_DATE and len(own) == round7.DRAWS_SAME_NAME
    assert "d1" not in own, "never the entry's own day"
    assert work == round7.draw_plan(entries, by_date, by_name)
