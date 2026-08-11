"""regime.classify_regime -- the ported flies-style regime tagging, adapted to MEIC's snapshot
shape. Pins: every dimension degrades to ('unknown', None) on missing inputs INCLUDING an empty
snapshot/params (the contract db.stale_writer_columns relies on to enumerate this module's column
set without a live snapshot), buckets are fraction-of-spot not points, and the two position-
dependent dimensions (skew, center_offset) need the chosen structure, not just the market."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from cherrypick.meic import regime  # noqa: E402

BUCKET_KEYS = tuple(f"{d}_bucket" for d in regime.DIMENSIONS)
VALUE_KEYS = tuple(f"{d}_value" for d in regime.DIMENSIONS)


def test_empty_snapshot_and_params_degrade_every_dimension_without_raising():
    out = regime.classify_regime({}, {})
    assert set(out.keys()) == set(BUCKET_KEYS) | set(VALUE_KEYS)
    for key in BUCKET_KEYS:
        assert out[key] == "unknown", key
    for key in VALUE_KEYS:
        assert out[key] is None, key


def test_vol_implied_buckets_on_iv_rank():
    assert regime.classify_regime({"iv_rank": 0.20}, {})["vol_implied_bucket"] == "low"
    assert regime.classify_regime({"iv_rank": 0.45}, {})["vol_implied_bucket"] == "normal"
    assert regime.classify_regime({"iv_rank": 0.80}, {})["vol_implied_bucket"] == "high"
    out = regime.classify_regime({"iv_rank": 0.20}, {})
    assert out["vol_implied_value"] == 0.20


def test_vol_event_reuses_the_live_gate_threshold_by_default():
    params = {"regime_vix1d_ratio_pause_threshold": 1.30}
    assert regime.classify_regime({"vix1d_ratio": 1.35}, params)["vol_event_bucket"] == "event"
    assert regime.classify_regime({"vix1d_ratio": 0.60}, params)["vol_event_bucket"] == "compression"
    assert regime.classify_regime({"vix1d_ratio": 1.0}, params)["vol_event_bucket"] == "normal"


def test_vol_event_survives_an_explicit_null_threshold_in_config():
    """config.example.json ships several regime_* thresholds as explicit `null` when the
    matching gate is off; .get(key, default) would silently return None (not the default) for a
    present-but-null key, so this must use `or` -- pin it stays non-degenerate either way."""
    params = {"regime_vix1d_ratio_pause_threshold": None}
    out = regime.classify_regime({"vix1d_ratio": 1.35}, params)
    assert out["vol_event_bucket"] == "event"  # falls back to the 1.30 default, doesn't crash


def test_vol_realized_uses_atr_over_spot_not_raw_points():
    # Same 50-point ATR reads differently on SPX (~7500) vs IWM (~300): a raw-points band would
    # silently mean two different things.
    spx = regime.classify_regime({"atr_5day": 75.0, "underlying_price": 7500.0}, {})  # 1.0%: normal
    iwm = regime.classify_regime({"atr_5day": 50.0, "underlying_price": 300.0}, {})  # 16.7%: high
    assert spx["vol_realized_bucket"] == "normal"
    assert iwm["vol_realized_bucket"] == "high"


def test_vol_realized_survives_null_pause_threshold():
    params = {"regime_atr_pause_threshold_pct": None}
    out = regime.classify_regime({"atr_5day": 200.0, "underlying_price": 7500.0}, params)
    assert out["vol_realized_bucket"] == "high"  # falls back to 0.015, doesn't crash on None


def test_vol_intraday_is_distinct_from_vol_realized():
    out = regime.classify_regime(
        {"atr_5day": 50.0, "intraday_range_pct": 0.01, "underlying_price": 7500.0}, {}
    )
    assert out["vol_realized_bucket"] != "unknown"
    assert out["vol_intraday_bucket"] == "high"


def _ok_gex(flip, spot):
    return {"gex": {"ok": True, "gamma_flip": flip, "spot": spot}, "underlying_price": spot}


def test_gex_deep_positive_near_flip_and_negative():
    deep = regime.classify_regime(_ok_gex(7000.0, 7100.0), {})
    near = regime.classify_regime(_ok_gex(7098.0, 7100.0), {})
    neg = regime.classify_regime(_ok_gex(7200.0, 7100.0), {})
    assert deep["gex_bucket"] == "deep_positive"
    assert near["gex_bucket"] == "near_flip"
    assert neg["gex_bucket"] == "negative"


def test_gex_unknown_when_not_ok():
    out = regime.classify_regime({"gex": {"ok": False}}, {})
    assert out["gex_bucket"] == "unknown"
    assert out["gex_value"] is None


def test_gex_survives_null_flip_distance_threshold():
    params = {"regime_gex_min_flip_distance_pct": None}
    out = regime.classify_regime(
        {"gex": {"ok": True, "gamma_flip": 7000.0, "spot": 7100.0}, "underlying_price": 7100.0}, params
    )
    assert out["gex_bucket"] == "deep_positive"  # falls back to 0.005, doesn't crash


def test_skew_needs_both_quotes_or_reads_unknown():
    snap = {"underlying_price": 7000.0}
    assert regime.classify_regime(snap, {})["skew_bucket"] == "unknown"
    out = regime.classify_regime(
        snap, {}, put_quote={"bid": 2.9, "ask": 3.1}, call_quote={"bid": 0.9, "ask": 1.1}
    )
    assert out["skew_bucket"] == "put_skew"
    assert out["skew_value"] > 0


def test_center_offset_needs_both_strikes():
    snap = {"underlying_price": 7000.0}
    assert regime.classify_regime(snap, {})["center_offset_bucket"] == "unknown"
    above = regime.classify_regime(snap, {}, put_strike=6990.0, call_strike=7060.0)
    below = regime.classify_regime(snap, {}, put_strike=6930.0, call_strike=7000.0)
    assert above["center_offset_bucket"] == "above_spot"
    assert below["center_offset_bucket"] == "below_spot"


def test_trend_is_fraction_of_spot_not_points():
    # A 20-point move reads differently on SPX vs IWM: a raw-points band would mean two things.
    spx = regime.classify_regime({"underlying_price": 7520.0, "day_open": 7500.0}, {})
    iwm = regime.classify_regime({"underlying_price": 320.0, "day_open": 300.0}, {})
    assert spx["trend_bucket"] == "flat"  # 20/7500 = 0.27% < the 0.3% default band
    assert iwm["trend_bucket"] == "up_from_open"  # 20/300 = 6.7% >> the band


def test_trend_unknown_without_day_open():
    out = regime.classify_regime({"underlying_price": 7000.0}, {})
    assert out["trend_bucket"] == "unknown"


def test_regime_columns_prefixes_every_key():
    out = regime.regime_columns("entry", {"iv_rank": 0.5}, {})
    assert set(out.keys()) == {f"entry_{k}" for k in list(BUCKET_KEYS) + list(VALUE_KEYS)}
    assert out["entry_vol_implied_bucket"] == "normal"


def test_credit_richness():
    assert regime.credit_richness(2.0, 10.0) == 0.2
    assert regime.credit_richness(None, 10.0) is None
    assert regime.credit_richness(2.0, 0) is None


def test_put_credit_fraction():
    assert regime.put_credit_fraction(1.5, 3.0) == 0.5
    assert regime.put_credit_fraction(None, 3.0) is None
    assert regime.put_credit_fraction(1.5, None) is None


def test_minutes_to_close():
    assert regime.minutes_to_close("14:00") == 120
    assert regime.minutes_to_close("16:30") == -30
    assert regime.minutes_to_close(None) is None
    assert regime.minutes_to_close("not-a-time") is None


# --------------------------------------------------------------------------- market dimensions


def test_market_dimensions_exclude_the_structure_ones():
    """The split that makes an iteration row meaningful: a tick with no structure chosen can still
    be tagged along every MARKET dimension, and must not carry the two that describe a structure —
    those would be 'unknown' on every refused tick, a column degenerate by construction."""
    assert set(regime.MARKET_DIMENSIONS) | set(regime.STRUCTURE_DIMENSIONS) == set(regime.DIMENSIONS)
    assert not set(regime.MARKET_DIMENSIONS) & set(regime.STRUCTURE_DIMENSIONS)
    assert regime.STRUCTURE_DIMENSIONS == ("skew", "center_offset")


def test_market_regime_columns_tags_a_tick_with_no_structure():
    snapshot = {
        "iv_rank": 0.72,
        "vix1d_ratio": 1.45,
        "atr_5day": 120.0,
        "underlying_price": 6000.0,
        "intraday_range_pct": 0.001,
        "day_open": 5900.0,
        "gex": {"ok": True, "gamma_flip": 5800.0, "spot": 6000.0},
    }
    cols = regime.market_regime_columns(snapshot, {})

    # Every market dimension resolved to a real bucket without a single strike or quote in hand —
    # this is the whole reason a refused tick can be a denominator.
    assert cols["vol_implied_bucket"] == "high"
    assert cols["vol_event_bucket"] == "event"
    assert cols["vol_realized_bucket"] == "high"
    assert cols["vol_intraday_bucket"] == "low"
    assert cols["gex_bucket"] == "deep_positive"
    assert cols["trend_bucket"] == "up_from_open"
    assert cols["vol_implied_value"] == 0.72

    # ...and the structure dimensions are absent, not present-and-'unknown'.
    assert not [k for k in cols if k.startswith(("skew", "center_offset"))]


def test_market_regime_columns_degrades_on_an_empty_snapshot():
    """Same contract as classify_regime: an empty snapshot tags 'unknown', never raises. The
    iteration writer calls this on every tick including ones where the feed gave us nothing."""
    cols = regime.market_regime_columns({}, {})
    assert all(cols[f"{d}_bucket"] == "unknown" for d in regime.MARKET_DIMENSIONS)
    assert all(cols[f"{d}_value"] is None for d in regime.MARKET_DIMENSIONS)
