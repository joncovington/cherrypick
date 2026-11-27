"""Half days (an NYSE early close) are not tradeable: the week's entry is refused, never made up."""

from datetime import date, datetime

import pytest
from conftest import black_chain

from cherrypick.weeklyput import clock, db, paper_loop

HALF_DAY, EXPIRY = date(2026, 11, 27), date(2026, 12, 4)  # the day after Thanksgiving closes at 13:00
EXP = EXPIRY.isoformat()
YEARS = clock.years_to(HALF_DAY, EXPIRY)


def at(day: date, hh: int, mm: int) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=clock.ET)


@pytest.fixture
def conn(tmp_path):
    return db.connect(str(tmp_path / "ledger.db"))


@pytest.fixture
def stocked(cache):
    cache.last("SPX", 6800.0).last("SPY", 680.0)
    cache.chain("SPX", EXP, "SPXW", black_chain(6800.0, 0.16, YEARS, range(6000, 7001, 5)))
    cache.chain("SPY", EXP, "SPY", black_chain(680.0, 0.16, YEARS, range(600, 701)))
    return cache


def test_the_entry_session_on_a_half_day_sells_nothing_and_says_why(config, conn, stocked):
    """The guard: good quotes in the decision window (15 minutes before the 13:00 close) and still no
    sale. Remove the early-close branch in `run_once` and this fails with two open positions."""
    out = paper_loop.run_once(config, conn, cache_path=stocked.path, when=at(HALF_DAY, 12, 46))
    assert out["phase"] == "half_day" and out["cycle"] == EXP
    assert set(out["entries"].values()) == {"missed"}
    assert db.open_position_count(conn) == 0
    assert {e["refusal"] for e in db.entries(conn)} == {"half_day_no_entries"}


def test_the_refusal_stands_for_the_whole_session_and_is_not_made_up_later(config, conn, stocked):
    paper_loop.run_once(config, conn, cache_path=stocked.path, when=at(HALF_DAY, 9, 45))
    paper_loop.run_once(config, conn, cache_path=stocked.path, when=at(HALF_DAY, 12, 50))
    assert db.open_position_count(conn) == 0
    next_session = date(2026, 11, 30)
    out = paper_loop.run_once(config, conn, cache_path=stocked.path, when=at(next_session, 15, 46))
    assert out["cycle"] is None and db.open_position_count(conn) == 0


def test_an_ordinary_entry_session_still_sells(config, conn, cache):
    """The control: the same week's rule on a full day (one week earlier, a Friday) is untouched."""
    entry, expiry = date(2026, 11, 6), date(2026, 11, 13)
    years = clock.years_to(entry, expiry)
    cache.last("SPX", 6800.0).last("SPY", 680.0)
    cache.chain("SPX", expiry.isoformat(), "SPXW", black_chain(6800.0, 0.16, years, range(6000, 7001, 5)))
    cache.chain("SPY", expiry.isoformat(), "SPY", black_chain(680.0, 0.16, years, range(600, 701)))
    out = paper_loop.run_once(config, conn, cache_path=cache.path, when=at(entry, 15, 46))
    assert out["phase"] == "entry" and set(out["entries"].values()) == {"entered"}
