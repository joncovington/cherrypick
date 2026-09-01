"""Tests for the read-side threshold replay over `bwb_trigger_ticks`."""

from __future__ import annotations

import time

from cherrypick.bwb import db, replay


def _tick(ticked_at, *, abs_delta=None, spot=None, gamma_flip=None, addon=None):
    row = {
        "near_abs_delta": abs_delta,
        "spot": spot,
        "gamma_flip": gamma_flip,
        "ticked_at": ticked_at,
        "session_date": "2026-08-24",
    }
    if addon is not None:
        row["addon_short_bid"], row["addon_short_ask"] = addon[0]
        row["addon_long_bid"], row["addon_long_ask"] = addon[1]
    return row


def _cohort_row(**overrides):
    now = time.time()
    row = {
        "entry_session": "2026-08-24",
        "structure_signature": "sig1",
        "symbol": "SPX",
        "ticked_at": now,
        "session_date": "2026-08-24",
        "near_abs_delta": None,
        "peak_abs_delta": None,
        "spot": None,
        "gamma_flip": None,
        "gamma_flip_basis": None,
        "below_flip_seen": 0,
        "addon_short_bid": None,
        "addon_short_ask": None,
        "addon_long_bid": None,
        "addon_long_ask": None,
        "measured": 1,
        "refusal": None,
    }
    row.update(overrides)
    return row


# --------------------------------------------------------------------------- pure compute layer
def test_replay_at_base_thresholds_fires_delta_when_delta_crosses():
    ticks = [
        _tick(1, abs_delta=0.30, addon=((1.0, 1.2), (0.4, 0.6))),
        _tick(2, abs_delta=0.55, addon=((1.5, 1.7), (0.4, 0.6))),
        _tick(3, abs_delta=0.60, addon=((1.6, 1.8), (0.4, 0.6))),
    ]
    result = replay.replay_cohort_ticks(ticks, None)
    assert result["fires"]["delta"] is not None
    assert result["fires"]["delta"]["ticked_at"] == 2
    assert result["fires"]["delta"]["priceable"] is True
    assert result["fires"]["delta"]["addon_credit"] == 1.1  # (1.5+1.7)/2 - (0.4+0.6)/2


def test_replay_bounce_requires_peak_then_pullback():
    ticks = [
        _tick(1, abs_delta=0.30),
        _tick(2, abs_delta=0.55),  # peak crosses 0.50
        _tick(3, abs_delta=0.52),  # still above pullback bar (0.45)
        _tick(4, abs_delta=0.44, addon=((1.0, 1.2), (0.3, 0.5))),  # pulls back below 0.45 -> fires
    ]
    result = replay.replay_cohort_ticks(ticks, None)
    assert result["fires"]["bounce"] is not None
    assert result["fires"]["bounce"]["ticked_at"] == 4
    assert result["fires"]["delta"]["ticked_at"] == 2  # delta fired earlier, at the touch


def test_replay_flip_requires_latch_then_reclaim():
    ticks = [
        _tick(1, spot=6100, gamma_flip=6000),  # above flip, no latch
        _tick(2, spot=5990, gamma_flip=6000),  # trades below -> latches
        _tick(3, spot=5995, gamma_flip=6000),  # reclaim not yet past buffer
        _tick(4, spot=6006, gamma_flip=6000, addon=((0.8, 1.0), (0.2, 0.4))),  # >= 6000*1.001
    ]
    result = replay.replay_cohort_ticks(ticks, None)
    assert result["fires"]["flip"] is not None
    assert result["fires"]["flip"]["ticked_at"] == 4


def test_missing_ticks_are_excluded_not_guessed():
    """An unmeasured tick (None delta) must not advance the peak or fire a trigger."""
    ticks = [
        _tick(1, abs_delta=0.60),  # would fire delta immediately...
    ]
    # ...but if the FIRST tick were unmeasured, nothing should fire on it.
    unmeasured_first = [_tick(0, abs_delta=None), *ticks]
    result = replay.replay_cohort_ticks(unmeasured_first, None)
    assert result["fires"]["delta"]["ticked_at"] == 1  # fired on the measured tick, not tick 0

    all_unmeasured = [_tick(0, abs_delta=None), _tick(1, abs_delta=None)]
    result2 = replay.replay_cohort_ticks(all_unmeasured, None)
    assert result2["fires"]["delta"] is None
    assert result2["final_peak_abs_delta"] is None


def test_fire_tick_with_incomplete_addon_quotes_is_unpriceable():
    ticks = [_tick(1, abs_delta=0.60)]  # no addon quotes recorded
    result = replay.replay_cohort_ticks(ticks, None)
    fire = result["fires"]["delta"]
    assert fire is not None
    assert fire["priceable"] is False
    assert fire["addon_credit"] is None


def test_widened_threshold_delays_or_prevents_delta_fire():
    ticks = [_tick(1, abs_delta=0.30), _tick(2, abs_delta=0.42), _tick(3, abs_delta=0.48)]
    base = replay.replay_cohort_ticks(ticks, None)
    assert base["fires"]["delta"] is None  # never reaches 0.50

    narrower = replay.replay_cohort_ticks(ticks, {"delta_trigger": 0.40})
    assert narrower["fires"]["delta"] is not None
    assert narrower["fires"]["delta"]["ticked_at"] == 2

    wider_still = replay.replay_cohort_ticks(ticks, {"delta_trigger": 0.60})
    assert wider_still["fires"]["delta"] is None


def test_narrower_bounce_pullback_fires_earlier():
    ticks = [
        _tick(1, abs_delta=0.55),  # peak set
        _tick(2, abs_delta=0.49),  # pullback of 0.06 from peak's trigger bar
        _tick(3, abs_delta=0.40),
    ]
    wide_pullback = replay.replay_cohort_ticks(ticks, {"bounce_pullback": 0.15})  # bar 0.35, never reached
    narrow_pullback = replay.replay_cohort_ticks(ticks, {"bounce_pullback": 0.01})  # bar 0.49

    assert narrow_pullback["fires"]["bounce"]["ticked_at"] == 2
    assert wide_pullback["fires"]["bounce"] is None


# --------------------------------------------------------------------------- DB-touching layer
def test_replay_thresholds_reads_cohort_from_db():
    conn = db.connect()
    db.record_trigger_tick(conn, _cohort_row(ticked_at=1.0, near_abs_delta=0.30))
    db.record_trigger_tick(conn, _cohort_row(ticked_at=2.0, near_abs_delta=0.55))

    result = replay.replay_thresholds(conn, entry_session="2026-08-24", structure_signature="sig1")
    assert result["entry_session"] == "2026-08-24"
    assert result["ticks_considered"] == 2
    assert result["fires"]["delta"]["ticked_at"] == 2.0


def test_validate_against_real_matches_when_no_real_positions():
    """With no real positions recorded for a cohort, validation reports ok with no_real_position —
    nothing to disagree about."""
    conn = db.connect()
    db.record_trigger_tick(conn, _cohort_row(ticked_at=1.0, near_abs_delta=0.60))

    result = replay.validate_against_real(conn, entry_session="2026-08-24", structure_signature="sig1")
    assert result["ok"] is True
    assert all(c["reason"] == "no_real_position" for c in result["mismatches"] + [])


def test_validate_against_real_reproduces_armed_book(config):
    """A synthetic recorded cohort: a `delta` book position that armed for real, matched by trigger
    ticks that cross the base delta_trigger. The base-threshold replay must reproduce that arm."""
    conn = db.connect()
    now = time.time()
    db.save_position(
        conn,
        {
            "position_id": "SPX:delta:2026-08-24",
            "symbol": "SPX",
            "book": "delta",
            "entry_session": "2026-08-24",
            "structure_signature": "sig1",
            "expiration": "2026-08-31",
            "body_strike": 5900,
            "near_strike": 5905,
            "far_strike": 5890,
            "armed_at": "2026-08-24T14:00:00-04:00",
            "arm_reason": "delta_trigger_met",
        },
    )
    db.record_trigger_tick(conn, _cohort_row(ticked_at=now, near_abs_delta=0.30))
    db.record_trigger_tick(conn, _cohort_row(ticked_at=now + 60, near_abs_delta=0.55))

    result = replay.validate_against_real(conn, entry_session="2026-08-24", structure_signature="sig1")
    replayed = replay.replay_cohort_ticks(db.trigger_ticks_for_cohort(conn, "2026-08-24", "sig1"), None)
    assert replayed["fires"]["delta"] is not None
    assert result["ok"] is True
    assert result["mismatches"] == []
