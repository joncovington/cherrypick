"""The shared ET clock.

These pin the properties the four module copies were each relying on, so the consolidation cannot
quietly change what a session date means for any of them.
"""

from datetime import datetime, timedelta, timezone

from cherrypick.core import clock


def test_now_et_is_aware_and_eastern():
    now = clock.now_et()
    assert now.tzinfo is not None
    assert now.utcoffset() in (timedelta(hours=-5), timedelta(hours=-4))  # EST / EDT


def test_now_iso_carries_the_offset_and_drops_microseconds():
    stamp = clock.now_iso()
    assert stamp.count(":") >= 3  # HH:MM:SS plus the offset's own colon
    assert "." not in stamp
    parsed = datetime.fromisoformat(stamp)
    assert parsed.tzinfo is not None


def test_today_iso_is_the_ET_date_not_the_machines():
    """The reason this exists: a late-evening Pacific run is already tomorrow in ET, and a session
    filed under the local date lands on the wrong day."""
    assert clock.today_iso() == clock.now_et().date().isoformat()
    assert len(clock.today_iso()) == 10


def test_minute_of_day_reads_the_moment_it_is_given():
    """It takes `when` rather than a clock, which is what lets a decision rule be handed the tick
    being evaluated instead of whenever the process happens to run."""
    assert clock.minute_of_day(datetime(2026, 8, 20, 0, 0)) == 0
    assert clock.minute_of_day(datetime(2026, 8, 20, 9, 30)) == 570
    assert clock.minute_of_day(datetime(2026, 8, 20, 16, 0)) == 960
    assert clock.minute_of_day(datetime(2026, 8, 20, 23, 59)) == 1439


def test_minute_of_day_ignores_the_zone_it_is_handed():
    """Callers compare against ET config windows, so they pass ET-aware datetimes; the function
    reads the wall-clock fields and does not convert. Pinned so a future 'helpful' conversion is a
    test failure rather than a silent one-hour shift in every entry window."""
    naive = datetime(2026, 8, 20, 10, 15)
    aware = datetime(2026, 8, 20, 10, 15, tzinfo=timezone.utc)
    assert clock.minute_of_day(naive) == clock.minute_of_day(aware) == 615


def test_hhmm_to_min_parses_and_falls_back():
    assert clock.hhmm_to_min("09:30", 0) == 570
    assert clock.hhmm_to_min("16:00", 0) == 960
    for junk in ("", "nonsense", "9", None, 930, "aa:bb"):
        assert clock.hhmm_to_min(junk, 555) == 555


def test_hhmm_to_min_does_not_clamp():
    """A config value out of range is returned as given rather than silently squashed — a window of
    '25:00' is a config error to surface, not one to reinterpret as midnight."""
    assert clock.hhmm_to_min("25:00", 0) == 1500
