"""Half days (an NYSE early close) are not tradeable: the week's entry is refused, never made up."""

from datetime import date, datetime

import pytest
from conftest import black_chain

from cherrypick.weeklyput import clock, db, paper_loop, stream_request

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
    # the decision journal says it too, one row per name, so it and the entries table agree
    rows = conn.execute("SELECT symbol, reason, accepted FROM weeklyput_decisions").fetchall()
    assert {(r["symbol"], r["reason"], r["accepted"]) for r in rows} == {
        ("SPX", "half_day_no_entries", 0),
        ("SPY", "half_day_no_entries", 0),
    }


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


# --------------------------------------------------------------------------- what the status says
def test_status_on_a_half_day_says_nothing_is_sold(config, conn, stocked, monkeypatch):
    """The guard: the status reported a 12:45 window and an entry today on the day the loop refuses it.
    Take the early-close branch out of `run_status` and this fails."""
    monkeypatch.setattr(paper_loop.clock, "now_et", lambda: at(HALF_DAY, 12, 46))
    st = paper_loop.run_status(config, conn, cache_path=stocked.path)
    assert st["half_day"] is True
    assert st["decision_window"] is None and st["entry_today"] is None
    assert st["entry_refused"] == "half_day_no_entries"
    assert st["next_cycle"] == EXP  # the calendar itself is unchanged


def test_status_on_an_ordinary_entry_session_still_gives_the_window(config, conn, stocked, monkeypatch):
    """The control: a full day's entry session reports its window and its entry, and no refusal."""
    entry = date(2026, 11, 6)
    monkeypatch.setattr(paper_loop.clock, "now_et", lambda: at(entry, 15, 46))
    st = paper_loop.run_status(config, conn, cache_path=stocked.path)
    assert st["half_day"] is False and st["entry_refused"] is None
    assert st["decision_window"] == "15:45-15:55 ET" and st["entry_today"] == "2026-11-13"


def test_the_stream_request_asks_for_nothing_of_a_cycle_entered_on_a_half_day(config, conn, stocked):
    """The session before the entry (the warm chain) and the entry session (the puts) are both off."""
    before = date(2026, 11, 25)  # the trading day before the half day
    for day in (before, HALF_DAY):
        want = stream_request.wanted(conn, config, stocked.path, day)
        assert want == {"expirations": {}, "legs": []}


def test_the_stream_request_still_asks_for_an_ordinary_cycle(config, conn, cache):
    """The control: the same call one week earlier names the cycle's expiry and its puts."""
    entry, expiry = date(2026, 11, 6), date(2026, 11, 13)
    years = clock.years_to(entry, expiry)
    cache.last("SPX", 6800.0).last("SPY", 680.0)
    cache.chain("SPX", expiry.isoformat(), "SPXW", black_chain(6800.0, 0.16, years, range(6000, 7001, 5)))
    cache.chain("SPY", expiry.isoformat(), "SPY", black_chain(680.0, 0.16, years, range(600, 701)))
    want = stream_request.wanted(conn, config, cache.path, entry)
    assert {sym: d for sym, d in want["expirations"].items()} == {
        "SPX": [expiry.isoformat()],
        "SPY": [expiry.isoformat()],
    }
    assert want["legs"]


def test_a_forced_run_on_a_half_day_still_refuses(config, conn, stocked):
    """`force` skips the window, never the half-day refusal: good quotes, a forced run at 10:00 on the
    half day, and still no sale. Put `not force and` back on the early-close branch in `run_once` and
    this fails with two open positions."""
    out = paper_loop.run_once(config, conn, cache_path=stocked.path, when=at(HALF_DAY, 10, 0), force=True)
    assert out["phase"] == "half_day" and set(out["entries"].values()) == {"missed"}
    assert db.open_position_count(conn) == 0
    assert {e["refusal"] for e in db.entries(conn)} == {"half_day_no_entries"}


def test_a_forced_run_on_an_ordinary_day_still_sells_outside_the_window(config, conn, cache):
    """The control: `force` still skips the window on a full day."""
    entry, expiry = date(2026, 11, 6), date(2026, 11, 13)
    years = clock.years_to(entry, expiry)
    cache.last("SPX", 6800.0).last("SPY", 680.0)
    cache.chain("SPX", expiry.isoformat(), "SPXW", black_chain(6800.0, 0.16, years, range(6000, 7001, 5)))
    cache.chain("SPY", expiry.isoformat(), "SPY", black_chain(680.0, 0.16, years, range(600, 701)))
    out = paper_loop.run_once(config, conn, cache_path=cache.path, when=at(entry, 10, 0), force=True)
    assert out["phase"] == "entry" and set(out["entries"].values()) == {"entered"}
