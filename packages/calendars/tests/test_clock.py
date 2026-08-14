"""The week anchors: entry day, front/back expirations, holiday shifts, and the structure tag."""

from datetime import date

from cherrypick.calendars import clock


def test_ordinary_week_is_dc_4_7():
    # 2026-08-17 is an ordinary Monday.
    entry = clock.entry_session(date(2026, 8, 17))
    assert entry == date(2026, 8, 17)
    front = clock.front_expiration(entry)
    back = clock.back_expiration(entry)
    assert front == date(2026, 8, 21)
    assert back == date(2026, 8, 24)
    assert clock.structure_tag(entry, front, back) == "dc_4_7"


def test_labor_day_week_enters_tuesday_as_dc_3_6():
    # Labor Day 2026-09-07: entry rolls to Tuesday, same Friday, next ordinary Monday.
    entry = clock.entry_session(date(2026, 9, 7))
    assert entry == date(2026, 9, 8)
    front = clock.front_expiration(entry)
    back = clock.back_expiration(entry)
    assert front == date(2026, 9, 11)
    assert back == date(2026, 9, 14)
    assert clock.structure_tag(entry, front, back) == "dc_3_6"


def test_week_before_a_monday_holiday_is_dc_4_8():
    # Entry 2026-08-31 (Mon): the following Monday is Labor Day, so the back leg shifts to Tuesday.
    entry = clock.entry_session(date(2026, 8, 31))
    assert entry == date(2026, 8, 31)
    front = clock.front_expiration(entry)
    back = clock.back_expiration(entry)
    assert front == date(2026, 9, 4)
    assert back == date(2026, 9, 8)
    assert clock.structure_tag(entry, front, back) == "dc_4_8"


def test_good_friday_week_front_lands_thursday():
    # Good Friday 2026-04-03: the front expiration walks back to Thursday.
    entry = clock.entry_session(date(2026, 3, 30))
    assert entry == date(2026, 3, 30)
    front = clock.front_expiration(entry)
    assert front == date(2026, 4, 2)
    back = clock.back_expiration(entry)
    assert back == date(2026, 4, 6)
    assert clock.structure_tag(entry, front, back) == "dc_3_7"


def test_next_entry_session_rolls_at_the_date_boundary_only():
    # Monday itself: this week's entry. Tuesday (of an ordinary week): next week's Monday.
    assert clock.next_entry_session(date(2026, 8, 17)) == date(2026, 8, 17)
    assert clock.next_entry_session(date(2026, 8, 18)) == date(2026, 8, 24)
    # Sunday before: still the coming Monday.
    assert clock.next_entry_session(date(2026, 8, 16)) == date(2026, 8, 17)


def test_next_entry_session_on_a_holiday_monday_is_the_tuesday():
    assert clock.next_entry_session(date(2026, 9, 7)) == date(2026, 9, 8)
    # And the Tuesday itself is still that week's entry day, not a roll to next week.
    assert clock.next_entry_session(date(2026, 9, 8)) == date(2026, 9, 8)


def test_week_plan_is_internally_consistent():
    plan = clock.week_plan(date(2026, 8, 17))
    assert plan == {
        "week_of": "2026-08-17",
        "entry_session": "2026-08-17",
        "front_expiration": "2026-08-21",
        "back_expiration": "2026-08-24",
        "structure": "dc_4_7",
    }


def test_hhmm_to_min_falls_back_on_junk():
    assert clock.hhmm_to_min("10:15", 0) == 615
    assert clock.hhmm_to_min(None, 600) == 600
    assert clock.hhmm_to_min("junk", 600) == 600
