"""The provider against the REAL cherrypick.core.streamcache DDL — an upstream schema change must
fail here, not silently produce empty snapshots (the flies provider-suite discipline)."""

import time

import pytest
from cherrypick.core import streamcache

from cherrypick.calendars import provider

FRONT = "2026-08-21"
BACK = "2026-08-24"


class _Opt:
    def __init__(self, streamer_symbol, occ, strike, otype, expiration, underlying="SPX"):
        self._d = {
            "streamer_symbol": streamer_symbol,
            "symbol": occ,
            "strike_price": strike,
            "option_type": otype,
            "expiration_date": expiration,
            "underlying_symbol": underlying,
        }
        self.streamer_symbol = streamer_symbol

    def model_dump(self, mode="json"):
        return dict(self._d)


def _seed_cache(tmp_path, *, spot=6500.0, quote_age=0.0, roots=("SPXW",)):
    cache = tmp_path / "stream_cache.db"
    conn = streamcache.connect(cache)
    now = time.time()
    conn.execute(
        "INSERT INTO stream_trades(symbol, last, change, volume, updated_at) VALUES (?,?,?,?,?)",
        ("SPX", spot, 0.0, 0.0, now),
    )
    chain = {}
    for expiration, tag in ((FRONT, "F"), (BACK, "B")):
        for i in range(-20, 21):
            strike = spot + 5 * i
            for otype in ("put", "call"):
                for root in roots:
                    sym = f".{root}{tag}{otype[0].upper()}{strike:g}"
                    occ = f"{root:<6}26{tag}{otype[0].upper()}{strike:08.0f}"
                    chain[sym] = _Opt(sym, occ, strike, otype, expiration)
    streamcache.write_chain(conn, chain)
    for sym in chain:
        conn.execute(
            "INSERT INTO stream_quotes(symbol, bid, ask, mid, bid_size, ask_size, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (sym, 19.8, 20.2, 20.0, 1, 1, now - quote_age),
        )
        conn.execute(
            "INSERT INTO stream_greeks(symbol, delta, gamma, theta, vega, rho, iv, price, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (sym, 0.3, 0.0, 0.0, 1.2, 0.0, 0.18, 20.0, now),
        )
    conn.commit()
    conn.close()
    return cache


def test_entry_snapshot_ok_and_root_filtered(tmp_path):
    cache = _seed_cache(tmp_path, roots=("SPXW", "SPX"))  # a third-Friday-style double listing
    snap = provider.build_entry_snapshot(cache, "SPX", FRONT, BACK, root="SPXW")
    assert snap["ok"]
    assert snap["spot"] == 6500.0
    assert all(e["occ_symbol"].startswith("SPXW") for e in snap["front"] + snap["back"])
    assert snap["quote_stats"]["fresh"] > 0


def test_entry_snapshot_refuses_when_only_the_am_root_is_listed(tmp_path):
    cache = _seed_cache(tmp_path, roots=("SPX",))
    snap = provider.build_entry_snapshot(cache, "SPX", FRONT, BACK, root="SPXW")
    assert snap == {"ok": False, "symbol": "SPX", "reason": "not_weekly_listed"}


def test_entry_snapshot_refuses_a_missing_chain(tmp_path):
    cache = _seed_cache(tmp_path)
    snap = provider.build_entry_snapshot(cache, "SPX", FRONT, "2026-08-31", root="SPXW")
    assert snap["reason"] == "no_back_chain"


def test_entry_snapshot_refuses_stale_quotes(tmp_path):
    cache = _seed_cache(tmp_path, quote_age=9999)
    snap = provider.build_entry_snapshot(cache, "SPX", FRONT, BACK, root="SPXW")
    assert snap["ok"] is False
    assert snap["reason"] == "no_fresh_quotes"
    assert snap["rejected"] > 0


def test_mark_snapshot_ok_and_spread(tmp_path):
    cache = _seed_cache(tmp_path)
    legs = [
        {"streamer_symbol": ".SPXWFP6400", "position_symbol": "SPX"},
        {"streamer_symbol": ".SPXWBP6400", "position_symbol": "SPX"},
    ]
    snap = provider.build_mark_snapshot(cache, legs)
    assert snap["ok"]
    assert snap["spot"] == 6500.0
    assert snap["fresh"] == 2
    assert snap["max_spread_pct"] == pytest.approx(0.4 / 20.0, abs=1e-4)
    assert snap["greeks"][".SPXWFP6400"]["vega"] == 1.2


def test_mark_snapshot_refuses_a_missing_leg_but_reports_the_rest(tmp_path):
    cache = _seed_cache(tmp_path)
    legs = [
        {"streamer_symbol": ".SPXWFP6400", "position_symbol": "SPX"},
        {"streamer_symbol": ".NEVER_SUBSCRIBED", "position_symbol": "SPX"},
    ]
    snap = provider.build_mark_snapshot(cache, legs)
    assert snap["ok"] is False
    assert snap["reason"] == "missing_leg_quotes"
    assert snap["quotes"][".SPXWFP6400"] is not None
    assert snap["quotes"][".NEVER_SUBSCRIBED"] is None


def test_read_spot_staleness_gate(tmp_path):
    cache = _seed_cache(tmp_path)
    assert provider.read_spot(cache, "SPX", max_age_seconds=300) == 6500.0
    assert provider.read_spot(cache, "SPX", max_age_seconds=-1) is None


def test_occ_root():
    assert provider.occ_root("SPXW  260821P06400000") == "SPXW"
    assert provider.occ_root("SPX   260821P06400000") == "SPX"
    assert provider.occ_root(None) == ""
