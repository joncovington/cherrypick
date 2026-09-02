"""The provider against the real streamcache DDL: the deep window, refusal taxonomy, roots."""

import pytest

from cherrypick.pmcc import provider

PLAN = {
    "short_expiration": "2026-08-28",
    "long_expiration": "2026-09-04",
    "short_dte": 9,
    "long_dte": 16,
}


def test_missing_cache_refuses(tmp_path):
    snap = provider.build_entry_snapshot(tmp_path / "absent.db", "TNA", PLAN, root="TNA")
    assert not snap["ok"]
    assert snap["reason"] == "stream_cache_missing"


def test_no_spot_refuses(cache):
    snap = provider.build_entry_snapshot(cache.path, "TNA", PLAN, root="TNA")
    assert snap["reason"] == "no_spot_price"


def test_missing_chain_names_which_side(cache):
    cache.spot("TNA", 70.60)
    cache.option("TNA", "2026-08-28", 67.0, bid=4.70, ask=4.80)
    snap = provider.build_entry_snapshot(cache.path, "TNA", PLAN, root="TNA")
    assert snap["reason"] == "no_long_chain"


def test_adjusted_root_is_filtered(cache):
    cache.spot("TNA", 70.60)
    # Only the post-split adjusted root lists this date -> not_root_listed, never priced.
    cache.option("TNA", "2026-08-28", 67.0, root="TNA1", bid=4.70, ask=4.80)
    cache.option("TNA", "2026-09-04", 50.0, root="TNA1", bid=20.60, ask=20.80)
    snap = provider.build_entry_snapshot(cache.path, "TNA", PLAN, root="TNA")
    assert snap["reason"] == "not_root_listed"


def test_deep_window_includes_the_85_delta_strike(cache):
    cache.spot("TNA", 70.60)
    # The default 20% window floor is ~56.48 — the deep long at 60 is INSIDE, a 40 strike is
    # outside (2026-08-23 redesign: the shallower 85-90-delta long needs a much narrower window
    # than the old ~99-delta design's 45%).
    cache.option("TNA", "2026-08-28", 67.0, bid=4.70, ask=4.80)
    cache.option("TNA", "2026-09-04", 60.0, bid=10.60, ask=10.80, delta=0.88)
    deep_out = cache.option("TNA", "2026-09-04", 40.0, bid=30.60, ask=30.80)
    snap = provider.build_entry_snapshot(cache.path, "TNA", PLAN, root="TNA")
    assert snap["ok"], snap
    assert any(q for q in snap["quotes"])
    assert deep_out not in snap["quotes"]  # outside the window: metadata yes, quote fetch no
    long_sym = [e["streamer_symbol"] for e in snap["long_chain"] if e["strike_price"] == 60.0][0]
    assert snap["quotes"][long_sym]["mid"] == pytest.approx(10.70)
    assert snap["greeks"][long_sym]["delta"] == 0.88


def test_stale_and_crossed_quotes_rejected(cache):
    cache.spot("TNA", 70.60)
    cache.option("TNA", "2026-08-28", 67.0, bid=4.70, ask=4.80, age=9999)
    cache.option("TNA", "2026-09-04", 60.0, bid=11.00, ask=10.00)  # crossed
    snap = provider.build_entry_snapshot(cache.path, "TNA", PLAN, root="TNA")
    assert not snap["ok"]
    assert snap["reason"] == "no_fresh_quotes"
    assert snap["rejected"] == 2


def test_mark_snapshot_partial_leg_is_position_level_refusal(cache):
    cache.spot("TNA", 70.60)
    quoted = cache.option("TNA", "2026-08-28", 67.0, bid=4.70, ask=4.80)
    legs = [
        {"streamer_symbol": quoted, "position_symbol": "TNA"},
        {"streamer_symbol": ".TNA_MISSING", "position_symbol": "TNA"},
    ]
    snap = provider.build_mark_snapshot(cache.path, legs)
    assert not snap["ok"]
    assert snap["reason"] == "missing_leg_quotes"
    assert snap["quotes"][quoted] is not None
    assert snap["quotes"][".TNA_MISSING"] is None


def test_read_spot_staleness_gate(cache):
    cache.spot("TNA", 70.60, age=600)
    assert provider.read_spot(cache.path, "TNA", max_age_seconds=300) is None
    assert provider.read_spot(cache.path, "TNA") == 70.60


def test_read_session(cache):
    cache.summary("TNA", "2026-08-17", o=70.0, h=71.0, low=69.5, c=None, prev=69.8)
    row = provider.read_session(cache.path, "TNA", "2026-08-17")
    assert row["day_low"] == 69.5
    assert row["prev_day_close"] == 69.8
    assert provider.read_session(cache.path, "TNA", "2026-08-18") is None
