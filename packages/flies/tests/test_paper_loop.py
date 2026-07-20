"""Tests for the paper session driver — the layer that supplies snapshots and persists results."""

import time
from datetime import datetime

import pytest
from test_engine import BASE_CONFIG
from test_provider import intrinsic_quotes, seed

import db as dbmod
import paper_loop
import provider


@pytest.fixture()
def conn(tmp_path):
    return dbmod.connect(str(tmp_path / "paper_trades.db"))


def config(**defaults):
    return {
        "symbols": ["SPX"],
        "defaults": {**BASE_CONFIG["defaults"], "entry_modes": ["legged"], **defaults},
        "arms": {"control": {}},
    }


# A fixed Monday. The loop now refuses to act on a non-trading day, so a helper anchored on "today"
# would make every test in this file pass or fail depending on which day it was run.
TRADING_DAY = datetime(2026, 7, 20)


def at(hour, minute=0):
    """A datetime on a known trading day at a given ET wall-clock time."""
    return TRADING_DAY.replace(hour=hour, minute=minute, tzinfo=provider._ET)


# --------------------------------------------------------------------------- session gate
def test_out_of_hours_run_is_a_clean_no_op(cache_with_chain, conn):
    """Not merely 'nothing to do': outside RTH the cache holds yesterday's frozen quotes, and an
    iteration against those would manufacture fills at prices that no longer exist."""
    result = paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain), when=at(3))
    assert result["skipped"] == "outside_rth"
    assert conn.execute("SELECT COUNT(*) FROM fly_positions").fetchone()[0] == 0


def test_in_session_boundaries():
    assert paper_loop.in_session(9 * 60 + 30) is True
    assert paper_loop.in_session(9 * 60 + 29) is False
    assert paper_loop.in_session(15 * 60 + 59) is True
    assert paper_loop.in_session(16 * 60) is False, "the close is the end, not another iteration"


def test_force_overrides_the_session_gate(cache_with_chain, conn):
    result = paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain),
                                 when=at(3), force=True)
    assert "skipped" not in result


# --------------------------------------------------------------------------- happy path
def test_iteration_opens_a_position_and_records_it(cache_with_chain, conn):
    result = paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain), when=at(12))
    assert result["ok"] and result["iterations"] == 1
    rows = conn.execute("SELECT * FROM fly_positions").fetchall()
    assert len(rows) == 1
    assert rows[0]["arm"] == "control" and rows[0]["symbol"] == "SPX"


def test_missing_snapshot_is_logged_not_fatal(tmp_path, conn):
    """A streamer still warming up is an ordinary condition, not an outage. The loop has to survive
    it every morning."""
    result = paper_loop.run_once(config(), conn, cache_path=str(tmp_path / "absent.db"), when=at(12))
    assert result["ok"] is True
    assert result["results"][0]["reason"] == "stream_cache_missing"


# --------------------------------------------------------------------------- settlement
def test_settle_uses_last_trade_by_default(cache_with_chain, conn):
    paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain), when=at(12))
    result = paper_loop.run_settle(config(), conn, cache_path=str(cache_with_chain), when=at(16, 15))
    assert result["results"][0]["settlement_source"] == "last_trade"
    assert conn.execute(
        "SELECT COUNT(*) FROM fly_positions WHERE status = 'settled'").fetchone()[0] == 1


def test_explicit_settlement_price_wins(cache_with_chain, conn):
    """The last streamed trade approximates the official print but is not it. A position centred within
    a point of spot can settle on the wrong side of its centre because of that difference, so the
    override exists and is recorded as such."""
    paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain), when=at(12))
    result = paper_loop.run_settle(config(), conn, cache_path=str(cache_with_chain),
                                   when=at(16, 15), price=6123.45)
    assert result["results"][0]["settlement_source"] == "explicit"
    row = conn.execute("SELECT settlement_price FROM fly_positions").fetchone()
    assert row["settlement_price"] == 6123.45


def test_settle_without_a_price_refuses(tmp_path, conn):
    result = paper_loop.run_settle(config(), conn, cache_path=str(tmp_path / "absent.db"),
                                   when=at(16, 15))
    assert result["results"][0]["reason"] == "no_settlement_price"


# --------------------------------------------------------------------------- status
def test_status_is_file_only_and_reports_the_upstream_cache(cache_with_chain, conn):
    """This runs on a watchdog path, so it must never touch the broker or the network."""
    status = paper_loop.run_status(config(), conn, cache_path=str(cache_with_chain))
    assert status["ok"] and status["stream_cache_present"] is True
    assert status["positions_today"] == 0

    status = paper_loop.run_status(config(), conn, cache_path="/no/such/cache.db")
    assert status["stream_cache_present"] is False


# --------------------------------------------------------------------------- config resolution
def test_stream_cache_path_prefers_config_and_expands_tildes():
    """Portable paths only — no machine-specific absolutes anywhere in the suite."""
    path = paper_loop.stream_cache_path({"source": {"stream_cache_db": "~/somewhere/cache.db"}})
    assert "~" not in path and path.endswith("cache.db")


def test_stream_cache_path_defaults_to_meics_managed_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    path = paper_loop.stream_cache_path({})
    assert path == str(tmp_path / "data" / "meic" / "stream_cache.db")


# --------------------------------------------------------------------------- fixtures
@pytest.fixture()
def cache_with_chain(tmp_path):
    """A stream cache holding a fresh 0DTE SPX chain, spot slightly below the 6000 strike so the
    control arm's put-side leg-in is the one offered."""
    import sqlite3

    from cherrypick.core.streamcache import DDL

    path = tmp_path / "stream_cache.db"
    conn = sqlite3.connect(path)
    conn.executescript(DDL)
    conn.commit()
    conn.close()
    # Expiry is the fixed trading day these tests run against, so dte is 0 for every `at(...)`.
    seed(path, spot=5998.0, strikes=[5990, 5995, 6000, 6005, 6010],
         expiration=TRADING_DAY.date().isoformat(), quote_for=intrinsic_quotes(5998.0))
    return path


def test_fixture_sanity(cache_with_chain):
    """Guards the fixture itself.

    Worth its own test because I got this wrong first time: seeding every strike with the same quote
    produces a chain that looks fine and prices every vertical at zero credit, so the entry gates
    reject everything and the tests above pass by never trading. Assert the chain actually offers a
    credit, not merely that a snapshot builds.
    """
    import engine
    snap = provider.build_snapshot(cache_with_chain, "SPX", when=at(12))
    assert snap["ok"] is True and snap["dte"] == 0

    enter, reason, plan = engine.evaluate_credit_spread_entry(
        snap, engine.merged_params(config(), "control"), [])
    assert enter, f"fixture offers no tradeable credit ({reason})"
    assert plan["credit"] > 0


# --------------------------------------------------------------------------- settlement self-trigger
@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Redirect the cherrypick home so EOD reports land in tmp, never the real logs directory."""
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    return tmp_path / "logs" / "flies"


def test_settle_time_defaults_and_config_override():
    assert paper_loop.settle_time_min({}) == paper_loop.DEFAULT_SETTLE_MIN
    assert paper_loop.settle_time_min({"defaults": {"settle_time": "16:45"}}) == 16 * 60 + 45


def test_after_the_close_the_loop_settles_itself(cache_with_chain, conn, home):
    """The reason there is no second scheduled task: `--once` past the settle time settles the day.
    Two tasks can drift apart — one fires, the other is disabled, and the books sit unsettled."""
    paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain), when=at(12))
    assert conn.execute("SELECT COUNT(*) FROM fly_positions WHERE status='open'").fetchone()[0] == 1

    out = paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain), when=at(16, 30))
    assert out.get("settled_session") is True
    assert conn.execute("SELECT COUNT(*) FROM fly_positions WHERE status='settled'").fetchone()[0] == 1
    assert (home / f"paper-eod-{TRADING_DAY.date().isoformat()}.md").exists()


def test_settlement_happens_exactly_once(cache_with_chain, conn, home):
    """The task fires every two minutes, so without a marker it would re-settle all evening."""
    paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain), when=at(12))
    first = paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain), when=at(16, 30))
    second = paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain), when=at(16, 32))

    assert first.get("settled_session") is True
    assert second.get("settled_session") is None, "second run must not re-settle"
    assert second.get("skipped") == "outside_rth"


def test_settlement_is_checked_before_the_rth_gate(cache_with_chain, conn, home):
    """The settle time is after the close, so an RTH-gated check would never reach it — the ordering
    inside run_once is load-bearing, not incidental."""
    out = paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain), when=at(16, 30))
    assert out.get("settled_session") is True
    assert (home / f"paper-eod-{TRADING_DAY.date().isoformat()}.md").exists()


def test_before_the_settle_time_an_out_of_hours_run_stays_a_no_op(cache_with_chain, conn, home):
    out = paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain), when=at(8))
    assert out.get("skipped") == "outside_rth"
    assert not (home / f"paper-eod-{TRADING_DAY.date().isoformat()}.md").exists()


def test_status_reports_whether_the_task_is_registered(cache_with_chain, conn, home):
    """An empty paper DB looks identical whether the loop is registered and quiet or nothing is
    scheduled at all. The watchdog needs to tell those apart."""
    status = paper_loop.run_status(config(), conn, cache_path=str(cache_with_chain))
    assert "scheduled_task" in status and isinstance(status["scheduled_task"], bool)
    assert status["task_name"] == paper_loop._TASK_NAME
    assert status["session_settled"] is False


def test_nothing_happens_on_a_non_trading_day(cache_with_chain, conn, home):
    """The task fires every two minutes forever, including weekends.

    Without a calendar guard the Saturday-evening tick finds the clock past the settle time and no
    report written for that date, "settles" a session that never happened, and emits
    paper-eod-<saturday>.md — which the suite digest would then ingest as a real session, because it
    discovers those files by filename alone.
    """
    saturday = datetime(2026, 7, 18, 16, 30, tzinfo=provider._ET)
    out = paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain), when=saturday)
    assert out["skipped"] == "not_a_trading_day"
    assert not (home / "paper-eod-2026-07-18.md").exists()
    assert conn.execute("SELECT COUNT(*) FROM fly_positions").fetchone()[0] == 0


def test_a_weekday_still_settles(cache_with_chain, conn, home):
    """The counterweight — the guard must not swallow real sessions."""
    monday = datetime(2026, 7, 20, 16, 30, tzinfo=provider._ET)
    out = paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain), when=monday)
    assert out.get("settled_session") is True
    assert (home / "paper-eod-2026-07-20.md").exists()


def test_market_holidays_are_skipped_too(cache_with_chain, conn, home):
    """Weekends are the obvious case; a holiday on a weekday is the one that would slip through."""
    import datetime as _dt

    from cherrypick.core import calendar as cal
    holiday = next(d for d in (_dt.date(2026, 1, 1), _dt.date(2026, 7, 3), _dt.date(2026, 12, 25))
                   if not cal.is_trading_day(d))
    when = datetime(holiday.year, holiday.month, holiday.day, 16, 30, tzinfo=provider._ET)
    out = paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain), when=when)
    assert out["skipped"] == "not_a_trading_day"


# --------------------------------------------------------------------------- stale settlement
def test_settlement_refuses_a_stale_price(cache_with_chain, conn, home):
    """Settlement is the most consequential price read in the module — it decides every position's
    P&L at once and cannot be undone. Every other read is staleness-gated; this one was not, so a
    stalled feed would have settled the whole session against an hours-old print without complaint.
    """
    import sqlite3
    paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain), when=at(12))
    c = sqlite3.connect(cache_with_chain)
    c.execute("UPDATE stream_trades SET updated_at = ?", (time.time() - 6000,))
    c.commit()
    c.close()

    out = paper_loop.run_settle(config(), conn, cache_path=str(cache_with_chain), when=at(16, 30))
    assert out["ok"] is False
    assert out["results"][0]["reason"] == "no_settlement_price"
    assert conn.execute(
        "SELECT COUNT(*) FROM fly_positions WHERE status='settled'").fetchone()[0] == 0


def test_a_refused_settlement_does_not_block_the_retry(cache_with_chain, conn, home):
    """The paper-eod file is the done-marker. Writing it after a refusal would mark the day finished,
    stop the loop retrying, and leave every position open under a report describing a session that
    never closed."""
    import sqlite3
    paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain), when=at(12))
    c = sqlite3.connect(cache_with_chain)
    c.execute("UPDATE stream_trades SET updated_at = ?", (time.time() - 6000,))
    c.commit()
    c.close()

    paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain), when=at(16, 30))
    day = TRADING_DAY.date().isoformat()
    assert not (home / f"paper-eod-{day}.md").exists(), "marker must not be written on refusal"
    assert paper_loop.session_already_settled(day, home) is False

    # Feed recovers; the very next tick settles the day with no operator action.
    c = sqlite3.connect(cache_with_chain)
    c.execute("UPDATE stream_trades SET updated_at = ?", (time.time(),))
    c.commit()
    c.close()
    out = paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain), when=at(16, 32))
    assert out.get("settled_session") is True
    assert (home / f"paper-eod-{day}.md").exists()


def test_an_explicit_price_bypasses_the_staleness_gate(cache_with_chain, conn, home):
    """The documented recovery path: settle with the official print when the feed is unavailable."""
    import sqlite3
    paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain), when=at(12))
    c = sqlite3.connect(cache_with_chain)
    c.execute("UPDATE stream_trades SET updated_at = ?", (time.time() - 6000,))
    c.commit()
    c.close()

    out = paper_loop.run_settle(config(), conn, cache_path=str(cache_with_chain),
                                when=at(16, 30), price=7455.72)
    assert out["ok"] is True
    assert out["results"][0]["settlement_source"] == "explicit"
