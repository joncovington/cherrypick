"""The status reports follow an NYSE early close: the session ends at 13:00, so it is not "in session"
at 13:30 that day. The loops keep their fixed 16:00 (the entry gate refuses an early close outright)."""

from datetime import date, datetime

from test_paper_loop import cache_with_chain, config, conn  # noqa: F401  (the fixtures the status tests use)

from cherrypick.flies import paper_loop, provider

HALF_DAY, FULL_DAY = date(2026, 11, 27), date(2026, 11, 25)  # the day after Thanksgiving; the day before
AFTERNOON = 13 * 60 + 30


def test_in_session_takes_the_days_own_close():
    """The guard: with a date, 13:30 is past a 13:00 close and inside a 16:00 one. Drop the date from
    `in_session` and the half day reads as open."""
    assert paper_loop.in_session(AFTERNOON, HALF_DAY) is False
    assert paper_loop.in_session(12 * 60 + 59, HALF_DAY) is True
    assert paper_loop.in_session(AFTERNOON, FULL_DAY) is True


def test_the_loops_without_a_date_keep_the_fixed_close():
    """The control: the loops pass no date and read 13:30 as in session, as they always have."""
    assert paper_loop.in_session(AFTERNOON) is True
    assert paper_loop.in_session(16 * 60) is False


def test_status_on_a_half_day_afternoon_is_not_in_session(cache_with_chain, conn, monkeypatch):  # noqa: F811
    monkeypatch.setattr(
        paper_loop.provider, "now_et", lambda: datetime(2026, 11, 27, 13, 30, tzinfo=provider._ET)
    )
    status = paper_loop.run_status(config(), conn, cache_path=str(cache_with_chain))
    assert status["date"] == "2026-11-27" and status["in_session"] is False
