"""The `wall-clear` arm: control plus no legged entry until spot has cleared the GEX wall on the
completing side (the call wall for a put spread, the put wall for a call spread)."""

from datetime import datetime

import pytest
from test_engine import _machine_config, params, snapshot

from cherrypick.flies import db as dbmod
from cherrypick.flies import engine, paper_loop, provider

ON = {"wall_clear_ahead_points": 10, "wall_clear_past_points": 5}


def walls(call_wall=6100.0, put_wall=5900.0, status="measured"):
    if status != "measured":
        return {"status": "unmeasured", "reason": status}
    return {"status": "measured", "call_wall": call_wall, "put_wall": put_wall, "age_seconds": 42.0}


def room_verdict(side, room):
    """The gate's verdict at a given room for `side`, spot fixed at 6000."""
    spot = 6000.0
    rec = walls(call_wall=spot + room) if side == engine.PUT else walls(put_wall=spot - room)
    return engine.wall_clearance_refusal(snapshot(recorded_gex=rec), params(**ON), side)


@pytest.mark.parametrize("side", [engine.PUT, engine.CALL])
def test_the_band_is_wall_close_ahead_or_crossed_by_less_than_the_clearance(side):
    reason = "call_wall_not_cleared" if side == engine.PUT else "put_wall_not_cleared"
    for room in (9.99, 5.0, 0.0, -4.99):
        assert room_verdict(side, room) == reason, room
    for room in (10.0, 25.0, -5.0, -12.0):
        assert room_verdict(side, room) is None, room


def test_each_side_reads_its_own_wall():
    """A put spread completes on a rally, so only the call wall matters to it; a put wall right under
    spot must not refuse it (and the mirror for a call spread)."""
    snap = snapshot(recorded_gex=walls(call_wall=6100.0, put_wall=5995.0))
    assert engine.wall_clearance_refusal(snap, params(**ON), engine.PUT) is None
    assert engine.wall_clearance_refusal(snap, params(**ON), engine.CALL) == "put_wall_not_cleared"
    snap = snapshot(recorded_gex=walls(call_wall=6005.0, put_wall=5900.0))
    assert engine.wall_clearance_refusal(snap, params(**ON), engine.CALL) is None
    assert engine.wall_clearance_refusal(snap, params(**ON), engine.PUT) == "call_wall_not_cleared"


def test_off_unless_an_arm_sets_it():
    snap = snapshot(recorded_gex=walls(call_wall=6002.0))
    assert engine.wall_clearance_refusal(snap, params(), engine.PUT) is None
    assert engine.wall_clearance_refusal(snap, params(wall_clear_ahead_points=None), engine.PUT) is None


@pytest.mark.parametrize("rec", [None, walls(status="stale_sample"), walls(call_wall=None)])
def test_unmeasured_walls_fail_open(rec):
    snap = snapshot() if rec is None else snapshot(recorded_gex=rec)
    assert engine.wall_clearance_refusal(snap, params(**ON), engine.PUT) is None


def test_the_entry_is_refused_with_the_gates_reason_and_control_is_untouched():
    """Shown to fail without the gate: spot 5998 under an ATM 6000 centre legs the put spread; with
    the call wall at 6005 (7 points ahead) control enters and wall-clear is refused, with the wall,
    room and sample age left in the gate detail the attempts ledger records."""
    snap = snapshot(underlying_price=5998.0, recorded_gex=walls(call_wall=6005.0))
    enter, _, plan = engine.evaluate_credit_spread_entry(snap, params(), [])
    assert enter and plan["side"] == engine.PUT
    detail: dict = {}
    enter, reason, plan = engine.evaluate_credit_spread_entry(snap, params(**ON), [], gate_detail=detail)
    assert (enter, reason, plan) == (False, "call_wall_not_cleared", None)
    assert detail == {"wall": 6005.0, "wall_room_points": 7.0, "wall_age_seconds": 42.0}
    # Once spot is 5+ points through the wall the same arm enters.
    snap = snapshot(underlying_price=5998.0, recorded_gex=walls(call_wall=5990.0))
    assert engine.evaluate_credit_spread_entry(snap, params(**ON), [])[0]


def test_the_snapshot_carries_the_recorders_walls_and_survives_their_absence(monkeypatch):
    when = datetime(2026, 10, 5, 11, 0, tzinfo=provider._ET)
    real = provider._regime.gex_at
    seen = {}

    def fake(ts, symbol, *, max_staleness_seconds):
        seen.update(ts=ts, symbol=symbol, max_age=max_staleness_seconds)
        return walls()

    monkeypatch.setattr(provider._regime, "gex_at", fake)
    assert provider._recorded_gex("SPX", when) == walls()
    assert seen == {"ts": when.timestamp(), "symbol": "SPX", "max_age": provider.RECORDED_GEX_MAX_AGE_SECONDS}

    def broken(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(provider._regime, "gex_at", broken)
    assert provider._recorded_gex("SPX", when) == {"status": "unmeasured", "reason": "read_failed: OSError"}
    # The real reader against the test's empty home: unmeasured, never a raise. Restored by hand, never
    # monkeypatch.undo(), which would also undo conftest's home redirect and read the real home.
    monkeypatch.setattr(provider._regime, "gex_at", real)
    assert provider._recorded_gex("SPX", when)["status"] == "unmeasured"


def test_wall_clear_is_registered():
    assert "wall-clear" in engine.ARMS


def test_this_machines_wall_clear_differs_from_control_in_one_variable():
    """One variable vs control (the gate's two thresholds are that variable); `enabled` is staging."""
    arms = _machine_config()["arms"]
    if "wall-clear" not in arms:
        pytest.skip("this machine does not run the wall-clear arm")

    def live_keys(d):
        return {k: v for k, v in d.items() if not k.startswith("_") and k != "enabled"}

    wc, ctl = live_keys(arms["wall-clear"]), live_keys(arms["control"])
    assert (wc.pop("wall_clear_ahead_points"), wc.pop("wall_clear_past_points")) == (10, 5)
    assert wc == ctl


def test_the_arm_is_journaled_once_when_enabled(tmp_path):
    conn = dbmod.connect(str(tmp_path / "paper_trades.db"))
    cfg = {"arms": {"wall-clear": {"enabled": True, **ON}}}
    paper_loop._note_wall_clear_arm(conn, cfg)
    paper_loop._note_wall_clear_arm(conn, cfg)
    rows = [r for r in dbmod.measurement_breaks(conn) if r["scope"] == "wall-clear"]
    assert len(rows) == 1 and rows[0]["kind"] == "arm_added" and rows[0]["break_date"] == "2026-10-19"
    staged = dbmod.connect(str(tmp_path / "staged.db"))
    paper_loop._note_wall_clear_arm(staged, {"arms": {"wall-clear": {"enabled": False, **ON}}})
    assert not [r for r in dbmod.measurement_breaks(staged) if r["scope"] == "wall-clear"]
