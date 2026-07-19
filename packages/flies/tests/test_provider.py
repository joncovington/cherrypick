"""Tests for the stream-cache snapshot provider.

Built against the real `cherrypick.core.streamcache` DDL rather than a hand-written mock table, so a
schema change upstream fails here instead of silently producing empty snapshots in production.
"""

import json
import sqlite3
import time
from datetime import datetime

import pytest
from cherrypick.core.streamcache import DDL

import provider


@pytest.fixture()
def cache(tmp_path):
    """A stream cache shaped exactly like the one MEIC's streamer writes."""
    path = tmp_path / "stream_cache.db"
    conn = sqlite3.connect(path)
    conn.executescript(DDL)
    conn.commit()
    conn.close()
    return path


TODAY = datetime.now(provider._ET).date().isoformat()


_DEFAULT_STRIKES = tuple(range(5980, 6021, 5))


def intrinsic_quotes(spot, extrinsic=2.0, width=0.4):
    """Quote builder giving each strike a plausible price: intrinsic value plus flat extrinsic.

    A constant quote across every strike looks harmless and is not — every vertical then prices at
    zero credit, so entry gates reject everything and a test can pass by never trading at all.
    """
    def build(strike, opt_type):
        intrinsic = max(0.0, (strike - spot) if opt_type == "P" else (spot - strike))
        mid = intrinsic + extrinsic
        return round(mid - width / 2, 2), round(mid + width / 2, 2)
    return build


def seed(cache_path, *, spot=6000.0, strikes=_DEFAULT_STRIKES, expiration=None,
         quote_age=0.0, symbol="SPX", oi=1000, gamma=0.001, bid_ask=(1.0, 1.2),
         quote_for=None):
    expiration = expiration or TODAY
    conn = sqlite3.connect(cache_path)
    now = time.time()
    if spot is not None:
        conn.execute("INSERT OR REPLACE INTO stream_trades (symbol, last, volume, updated_at) "
                     "VALUES (?, ?, ?, ?)", (symbol, spot, 0, now))
    for strike in strikes:
        for opt_type, tag in (("C", "C"), ("P", "P")):
            streamer_symbol = f".{symbol}{expiration.replace('-', '')}{tag}{strike}"
            conn.execute(
                "INSERT OR REPLACE INTO stream_chain "
                "(streamer_symbol, expiration, underlying_symbol, data_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (streamer_symbol, expiration, symbol, json.dumps({
                    "streamer_symbol": streamer_symbol, "strike_price": strike,
                    "option_type": opt_type, "shares_per_contract": 100}), now))
            bid, ask = quote_for(strike, tag) if quote_for else bid_ask
            conn.execute(
                "INSERT OR REPLACE INTO stream_quotes (symbol, bid, ask, mid, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (streamer_symbol, bid, ask, (bid + ask) / 2, now - quote_age))
            conn.execute("INSERT OR REPLACE INTO stream_greeks (symbol, gamma, updated_at) "
                         "VALUES (?, ?, ?)", (streamer_symbol, gamma, now))
            conn.execute("INSERT OR REPLACE INTO stream_oi (symbol, open_interest, updated_at) "
                         "VALUES (?, ?, ?)", (streamer_symbol, oi, now))
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------- happy path
def test_builds_an_engine_ready_snapshot(cache):
    seed(cache)
    snap = provider.build_snapshot(cache, "SPX")
    assert snap["ok"] is True
    assert snap["symbol"] == "SPX"
    assert snap["underlying_price"] == 6000.0
    assert snap["dte"] == 0
    assert 6000.0 in snap["puts"] and 6000.0 in snap["calls"]
    assert snap["puts"][6000.0]["bid"] == 1.0
    assert 0 <= snap["now_min"] < 24 * 60


def test_snapshot_feeds_the_engine_without_translation(cache):
    """The contract that matters: what the provider emits is what the engine consumes. A mismatch
    here would show up as an arm that silently never trades."""
    import engine
    seed(cache, bid_ask=(2.0, 2.4))
    snap = provider.build_snapshot(cache, "SPX")
    assert engine.quote(snap, "put", 6000.0) is not None
    center, _ = engine.select_center(snap, {"arm": "control", "strike_increment": 5})
    assert center == 6000.0


def test_gex_is_computed_over_the_whole_chain(cache):
    seed(cache)
    snap = provider.build_snapshot(cache, "SPX")
    assert snap["gex"]["ok"] is True
    assert snap["gex"]["strikes_with_data"] > 0


# --------------------------------------------------------------------------- the refusal paths
def test_stale_quotes_are_rejected_not_traded(cache):
    """A cached quote from twenty minutes ago would price a fill that could never have happened. On
    0DTE that is not a small error, and it is the kind that makes paper results look better than
    reality rather than worse."""
    seed(cache, quote_age=1200)
    snap = provider.build_snapshot(cache, "SPX", max_quote_age_seconds=120)
    assert snap["ok"] is False and snap["reason"] == "no_fresh_quotes"


def test_fresh_quotes_survive_the_same_gate(cache):
    seed(cache, quote_age=30)
    snap = provider.build_snapshot(cache, "SPX", max_quote_age_seconds=120)
    assert snap["ok"] is True
    assert snap["quote_stats"]["rejected"] == 0


def test_crossed_quotes_are_dropped(cache):
    """bid > ask is a torn read or a broken feed, never an opportunity."""
    seed(cache, bid_ask=(3.0, 1.0))
    snap = provider.build_snapshot(cache, "SPX")
    assert snap["ok"] is False and snap["reason"] == "no_fresh_quotes"


def test_missing_spot_refuses_rather_than_guessing(cache):
    """MEIC hit exactly this with RUT — a subscribed symbol that never streamed a Trade event."""
    seed(cache, spot=None)
    snap = provider.build_snapshot(cache, "SPX")
    assert snap["ok"] is False and snap["reason"] == "no_spot_price"


def test_missing_cache_is_reported_not_raised(cache, tmp_path):
    snap = provider.build_snapshot(tmp_path / "nope.db", "SPX")
    assert snap["ok"] is False and snap["reason"] == "stream_cache_missing"


def test_uncached_symbol_reports_no_chain(cache):
    seed(cache)
    snap = provider.build_snapshot(cache, "NDX")
    assert snap["ok"] is False and snap["reason"] in ("no_spot_price", "no_chain_cached")


def test_strikes_far_from_spot_are_excluded(cache):
    """Pulling a whole SPX chain every iteration would mean thousands of quote rows for structures no
    arm would ever centre on."""
    seed(cache, strikes=[5000, 6000, 7000])
    snap = provider.build_snapshot(cache, "SPX", strike_window_pct=0.015)
    assert set(snap["puts"]) == {6000.0}


def test_partial_staleness_keeps_the_fresh_legs(cache):
    """One stale leg should cost that structure, not the whole session."""
    seed(cache, strikes=[5995, 6000, 6005])
    conn = sqlite3.connect(cache)
    conn.execute("UPDATE stream_quotes SET updated_at = ? WHERE symbol LIKE '%P6005'",
                 (time.time() - 9999,))
    conn.commit()
    conn.close()

    snap = provider.build_snapshot(cache, "SPX", max_quote_age_seconds=120)
    assert snap["ok"] is True
    assert 6005.0 not in snap["puts"]
    assert 6000.0 in snap["puts"]
    assert snap["quote_stats"]["rejected"] == 1


# --------------------------------------------------------------------------- isolation
def test_provider_never_writes_to_the_streamers_cache(cache):
    """MEIC's streamer owns this file while it is live. A reader that could mutate it would be a
    reliability bug in someone else's module, so the connection is opened read-only."""
    seed(cache)
    before = cache.stat().st_mtime_ns
    provider.build_snapshot(cache, "SPX")
    assert cache.stat().st_mtime_ns == before

    conn = provider._connect_ro(cache)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM stream_trades")
    conn.close()


def test_spx_and_xsp_chains_are_never_blended(cache):
    """Both list 0DTE on the same dates and their strikes differ by 10x, so an expiration-only match
    would produce a chain that looks plausible and is nonsense."""
    seed(cache, symbol="SPX", spot=6000.0, strikes=[5995, 6000, 6005])
    seed(cache, symbol="XSP", spot=600.0, strikes=[595, 600, 605])
    snap = provider.build_snapshot(cache, "XSP")
    assert set(snap["puts"]) == {595.0, 600.0, 605.0}


def test_non_zero_dte_is_labelled_so_the_engine_can_refuse(cache):
    """The provider does not gate on 0DTE — it reports the DTE and lets the engine's own hard stop
    reject it, so there is exactly one place that decision lives."""
    import engine
    seed(cache, expiration="2099-01-15")
    snap = provider.build_snapshot(cache, "SPX")
    assert snap["dte"] > 0
    enter, reason, _ = engine.evaluate_credit_spread_entry(snap, {"arm": "control"}, [])
    assert not enter and reason == "no_0dte_expiration"
