"""Tests for the paper session driver — the layer that supplies snapshots and persists results."""

from datetime import datetime

import pytest
from test_engine import BASE_CONFIG
from test_provider import TODAY, intrinsic_quotes, seed

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


def at(hour, minute=0):
    """A datetime today at a given ET wall-clock time — the session gate reads minute-of-day."""
    now = provider.now_et()
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


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
    seed(path, spot=5998.0, strikes=[5990, 5995, 6000, 6005, 6010], expiration=TODAY,
         quote_for=intrinsic_quotes(5998.0))
    return path


def test_fixture_sanity(cache_with_chain):
    """Guards the fixture itself.

    Worth its own test because I got this wrong first time: seeding every strike with the same quote
    produces a chain that looks fine and prices every vertical at zero credit, so the entry gates
    reject everything and the tests above pass by never trading. Assert the chain actually offers a
    credit, not merely that a snapshot builds.
    """
    import engine
    snap = provider.build_snapshot(cache_with_chain, "SPX", when=datetime.now(provider._ET))
    assert snap["ok"] is True and snap["dte"] == 0

    enter, reason, plan = engine.evaluate_credit_spread_entry(
        snap, engine.merged_params(config(), "control"), [])
    assert enter, f"fixture offers no tradeable credit ({reason})"
    assert plan["credit"] > 0
