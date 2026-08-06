"""Which regime gates are armed — the read surface over the fail-open behaviour.

The gates deactivate silently when their data is missing (GATES.md: "If GEX data unavailable,
proceed without GEX"). That is deliberate, and these tests do not change it. What they pin is that
the *report* tells the truth about it — including the ATR case, where a multi-day outage leaves the
gate disarmed for another week after the streamer is healthy again.
"""

import sqlite3
from datetime import UTC, datetime

import pytest

from cherrypick.meic import gate_health as gh

NOW = datetime(2026, 8, 6, 10, 0, tzinfo=UTC)
NOW_TS = NOW.timestamp()
TODAY = "2026-08-06"


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """A stream cache with the two tables the gates actually read."""
    path = tmp_path / "stream_cache.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE stream_summary (symbol TEXT, trade_date TEXT, day_open REAL, day_high REAL, "
        "day_low REAL, day_close REAL, prev_day_close REAL, updated_at REAL)"
    )
    con.execute("CREATE TABLE stream_oi (symbol TEXT, open_interest INTEGER, updated_at REAL)")
    con.commit()
    con.close()
    monkeypatch.setattr(gh, "stream_cache_path", lambda: path)
    return path


def _sessions(path, symbol, dates):
    con = sqlite3.connect(path)
    con.executemany(
        "INSERT INTO stream_summary (symbol, trade_date) VALUES (?, ?)", [(symbol, d) for d in dates]
    )
    con.commit()
    con.close()


def _oi(path, rows, updated_at=NOW_TS):
    con = sqlite3.connect(path)
    con.executemany(
        "INSERT INTO stream_oi (symbol, open_interest, updated_at) VALUES (?, 100, ?)",
        [(s, updated_at) for s in rows],
    )
    con.commit()
    con.close()


def _gate(result, name):
    return next(g for g in result["gates"] if g["gate"] == name)


# --------------------------------------------------------------------------- the ATR week-long disarm
def test_atr_reports_how_much_history_is_still_missing(cache):
    """The failure this exists for: after a multi-day outage the gate stays inactive for another
    week, with nothing surfacing why. The count is the actionable part, not the verdict."""
    _sessions(cache, "SPX", ["2026-08-03", "2026-08-04"])
    atr = _gate(gh.for_symbol("SPX", {"regime_atr_lookback_days": 5}, NOW), "atr")
    assert atr["status"] == gh.DEGRADED
    assert atr["sessions_available"] == 2 and atr["sessions_missing"] == 3
    assert "2/5" in atr["reason"] and "rebuilt" in atr["reason"]


def test_atr_arms_once_the_history_is_there(cache):
    _sessions(cache, "SPX", [f"2026-08-0{d}" for d in (1, 2, 3, 4, 5)])
    atr = _gate(gh.for_symbol("SPX", {"regime_atr_lookback_days": 5}, NOW), "atr")
    assert atr["status"] == gh.ARMED and atr["sessions_missing"] == 0


def test_atr_excludes_todays_partial_session(cache):
    """Counted the same way `tt.cmd_get_atr` counts, so this report can never disagree with the gate
    it describes — today's row is partial and the gate refuses it."""
    _sessions(cache, "SPX", ["2026-08-03", "2026-08-04", "2026-08-05", TODAY])
    atr = _gate(gh.for_symbol("SPX", {"regime_atr_lookback_days": 5}, NOW), "atr")
    assert atr["sessions_available"] == 3


# --------------------------------------------------------------------------- GEX
def test_gex_matches_open_interest_by_occ_symbol(cache):
    """`stream_oi` holds OCC symbols (`.SPXW260806P6300`) and has no underlying column. Assuming one
    made this report a false DEGRADED against a cache holding thousands of SPX rows."""
    _oi(cache, [".SPXW260806P6300", ".SPXW260806C6400"])
    assert _gate(gh.for_symbol("SPX", {}, NOW), "gex")["status"] == gh.ARMED


def test_gex_is_degraded_with_no_open_interest(cache):
    _oi(cache, [".QQQ260806P500"])
    gex = _gate(gh.for_symbol("SPX", {}, NOW), "gex")
    assert gex["status"] == gh.DEGRADED and gex["reason"] == "no open interest cached"


def test_a_stale_cache_is_reported_but_still_counts_as_armed(cache):
    """The gate fires on whatever OI is cached, so calling a stale cache "degraded" here would
    disagree with what the loop actually does. Say both things instead."""
    _oi(cache, [".SPXW260806P6300"], updated_at=NOW.timestamp() - 3600)
    gex = _gate(gh.for_symbol("SPX", {}, NOW), "gex")
    assert gex["status"] == gh.ARMED and "stale" in gex["reason"] and "60m ago" in gex["reason"]


# --------------------------------------------------------------------------- intraday range
def test_intraday_range_needs_todays_row(cache):
    _sessions(cache, "SPX", ["2026-08-05"])
    assert _gate(gh.for_symbol("SPX", {}, NOW), "intraday_range")["status"] == gh.DEGRADED
    _sessions(cache, "SPX", [TODAY])
    assert _gate(gh.for_symbol("SPX", {}, NOW), "intraday_range")["status"] == gh.ARMED


# --------------------------------------------------------------------------- the whole report
def test_headline_counts_across_every_symbol(cache):
    _sessions(cache, "SPX", [f"2026-08-0{d}" for d in (1, 2, 3, 4, 5)] + [TODAY])
    _oi(cache, [".SPXW260806P6300"])
    out = gh.report(["SPX", "QQQ"], {"regime_atr_lookback_days": 5}, NOW)
    assert out["armed"] == 3 and out["total"] == 6  # SPX fully armed, QQQ has nothing
    assert out["headline"] == "3 of 6 regime gates armed"
    assert {d["symbol"] for d in out["degraded"]} == {"QQQ"}


def test_a_missing_cache_says_so_once(monkeypatch, tmp_path):
    """No cache at all means every data-backed gate is down. Reporting each one's absence as if it
    had been independently checked would read as three unrelated faults."""
    monkeypatch.setattr(gh, "stream_cache_path", lambda: tmp_path / "nope.db")
    out = gh.for_symbol("SPX", {}, NOW)
    assert [g["status"] for g in out["gates"]] == [gh.DEGRADED] * 3
    assert all("streamer running" in g["reason"] for g in out["gates"])


def test_it_never_writes_to_the_cache(cache):
    """A read surface. It opens the cache read-only, so a bug here cannot corrupt the file the
    entire suite prices from."""
    _oi(cache, [".SPXW260806P6300"])
    before = cache.stat().st_mtime_ns
    gh.report(["SPX"], {}, NOW)
    assert cache.stat().st_mtime_ns == before
