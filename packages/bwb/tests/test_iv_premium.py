"""Implied at entry against realised to expiry (`iv_premium.py`, 2026-10-09): one window per
(entry session, expiration), measured from the recorded spot path, refused rather than estimated
when the path does not cover the window."""

from __future__ import annotations

import math
from datetime import datetime

import pytest
from test_live_loop import _plan

from cherrypick.bwb import book as bookmod
from cherrypick.bwb import db, iv_premium
from cherrypick.bwb.iv_premium import ET

ENTRY = datetime(2026, 9, 17, 10, 0, tzinfo=ET)  # Thursday, for Friday's expiry
EXP = "2026-09-18"
START = ENTRY.timestamp()
END = iv_premium.close_ts(EXP)


def _path(step_s=60, move=0.001, *, start=START, end=END, skip=None):
    """In-session ticks from `start` to `end`, alternating +/- `move` log returns."""
    out, t, s, sign = [], start, 6000.0, 1
    while t <= end:
        hour = datetime.fromtimestamp(t, ET)
        in_session = (hour.hour, hour.minute) >= (9, 30) and hour.hour < 16
        if in_session and not (skip and skip[0] <= t < skip[1]):
            out.append((t, s))
            s *= math.exp(sign * move)
            sign = -sign
        t += step_s
    return out


def test_realised_is_the_sum_of_squared_log_returns_ending_on_the_settlement_print():
    path = _path(step_s=300)
    got = iv_premium.realised(path, start_ts=START, end_ts=END, end_spot=path[-1][1] * math.exp(0.002))
    # every in-path return is 0.001 except the overnight one (also 0.001 by construction), plus the print
    assert got["coverage"] is None
    assert got["total_var"] == pytest.approx((len(path) - 1) * 0.001**2 + 0.002**2)
    assert got["returns"] == len(path)


def test_a_path_that_misses_the_window_is_refused_whatever_the_print_says():
    late = _path(start=START + 3600)
    assert iv_premium.realised(late, start_ts=START, end_ts=END, end_spot=6000)["coverage"] == "starts_late"
    early = _path(end=END - 4 * 3600)
    assert iv_premium.realised(early, start_ts=START, end_ts=END, end_spot=6000)["coverage"] == "ends_early"
    holed = _path(skip=(START + 3600, START + 3 * 3600))
    assert iv_premium.realised(holed, start_ts=START, end_ts=END, end_spot=6000)["coverage"] == "session_gap"
    assert iv_premium.realised([], start_ts=START, end_ts=END, end_spot=6000)["coverage"] == "no_path"


def test_coverage_is_judged_on_the_ticks_not_the_sampling_choice():
    got = iv_premium.realised(_path(), start_ts=START, end_ts=END, end_spot=None, sample_seconds=1800)
    assert got["coverage"] is None and got["max_gap_min"] == 1.0


def _settled(conn, config, arm, *, iv_vol=None, complete=None, session="2026-09-17", status="closed"):
    plan = {**_plan(), "expiration": EXP}
    opened = bookmod.enter_position(
        conn,
        plan,
        config,
        arm,
        entry_session=session,
        advice_params=None,
        implied=(
            {"entry_iv_vol": iv_vol, "entry_iv_var": (iv_vol / 100) ** 2, "entry_iv_complete": complete}
            if iv_vol is not None
            else None
        ),
    )
    pid = opened["position_id"]
    db.save_position(
        conn,
        {"position_id": pid, "entry_time": ENTRY.isoformat(), "status": status, "settlement_spot": 6000.0},
    )
    return pid


def test_arms_share_one_window_and_the_premium_is_implied_minus_realised(config):
    conn = db.connect()
    control = _settled(conn, config, "control", iv_vol=30.0, complete=1)
    _settled(conn, config, "delta", iv_vol=30.0, complete=1)
    _settled(conn, config, "bounce", session="2026-09-16", status="open")  # not settled: not a window
    for t, s in _path():
        conn.execute(
            "INSERT INTO bwb_marks(position_id, leg_role, marked_at, session_date, spot) VALUES (?,?,?,?,?)",
            (control, "body_short_1", t, "2026-09-17", s),
        )
        conn.execute(  # a second leg on the same tick: the same spot, one point
            "INSERT INTO bwb_marks(position_id, leg_role, marked_at, session_date, spot) VALUES (?,?,?,?,?)",
            (control, "far_long", t, "2026-09-17", s),
        )
    conn.commit()
    assert len(iv_premium.spot_path(conn, control)) == len(_path())  # one point per tick
    out = iv_premium.run(conn)
    (w,) = out["windows"]
    assert w["arms"] == 2 and w["position_id"] == control and w["coverage"] is None
    assert w["premium_vol"] == pytest.approx(30.0 - w["realised_vol"], abs=1e-3)
    assert out["summary"]["with_implied"]["windows"] == 1
    assert out["summary"]["with_complete_implied"]["windows"] == 1


def test_a_cut_off_strip_stays_out_of_the_complete_only_figures(config):
    conn = db.connect()
    pid = _settled(conn, config, "control", iv_vol=25.0, complete=0)
    for t, s in _path():
        conn.execute(
            "INSERT INTO bwb_marks(position_id, leg_role, marked_at, session_date, spot) VALUES (?,?,?,?,?)",
            (pid, "body_short_1", t, "2026-09-17", s),
        )
    conn.commit()
    summary = iv_premium.run(conn)["summary"]
    assert summary["with_implied"]["windows"] == 1 and summary["with_complete_implied"]["windows"] == 0
