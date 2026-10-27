"""The `limit-*` arms (research item 21, 2026-10-27): control, with the entry resting at a limit
above mid and filled only by a later tick that reaches it (`engine.entry_limit_settings`)."""

from datetime import datetime, timedelta

import pytest
from test_book import one_arm_config
from test_engine import params, q, snapshot

from cherrypick.flies import book as bookmod
from cherrypick.flies import clock, engine
from cherrypick.flies import db as dbmod

NOON = datetime(2026, 7, 20, 12, 0, tzinfo=clock.ET)
SPOT = 5998.0  # centre 6000, put spread 6000/5995: mid credit 5.2 - 2.8 = 2.40, control books 2.30
RICHER = {6000: q(5.1, 5.5), 5995: q(2.6, 3.0), 5990: q(1.0, 1.4), 6005: q(8.6, 9.0)}  # mid 2.50


@pytest.fixture()
def conn(tmp_path):
    return dbmod.connect(str(tmp_path / "paper_trades.db"))


def tick(conn, cfg, arm, seconds, **over):
    with clock.pinned(NOON + timedelta(seconds=seconds)):
        return bookmod.process_snapshot(snapshot(underlying_price=SPOT, **over), cfg, conn, arm)


def config(arm, offset, window=120):
    cfg = one_arm_config(entry_modes=["legged"])
    cfg["arms"] = {arm: {"entry_limit": {"offset": offset, "window_seconds": window}}}
    return cfg


def positions(conn, arm):
    return dbmod.book_positions(conn, bookmod.book_id_for("2026-07-20", arm, "SPX"))


def orders(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM fly_resting_entries ORDER BY id")]


def test_the_settings_are_off_unless_an_arm_sets_them_and_a_bad_block_raises():
    assert engine.entry_limit_settings(params()) is None
    assert engine.entry_limit_settings(params(entry_limit={"offset": 0.05})) == {
        "offset": 0.05,
        "window_seconds": 120.0,
        "basis": "mid",
    }
    for bad in ({"offset": -0.05}, {"window_seconds": 0}, {"basis": "ask"}):
        with pytest.raises(ValueError):
            engine.entry_limit_settings(params(entry_limit=bad))


def test_the_limit_is_mid_plus_the_offset_floored_to_the_tick():
    _, _, plan = engine.evaluate_credit_spread_entry(snapshot(underlying_price=SPOT), params(), [])
    assert plan["credit"] == pytest.approx(2.30)  # control's modelled credit
    snap = snapshot(underlying_price=SPOT)
    assert engine.entry_limit_price(snap, plan, 0.0) == pytest.approx(2.40)
    assert engine.entry_limit_price(snap, plan, 0.05) == pytest.approx(2.45)
    assert engine.entry_limit_price(snap, plan, 0.07) == pytest.approx(2.45)


def test_control_books_on_the_plan_tick_and_the_limit_arm_rests(conn):
    """Shown to fail without the rule: on the same tick control opens a position and the limit arm
    opens none, holding one working order instead."""
    tick(conn, one_arm_config(entry_modes=["legged"]), "control", 0)
    assert [r["credit"] for r in positions(conn, "control")] == [pytest.approx(2.30)]
    tick(conn, config("limit-005", 0.05), "limit-005", 0)
    assert positions(conn, "limit-005") == []
    assert [(o["status"], o["limit_credit"]) for o in orders(conn)] == [("resting", pytest.approx(2.45))]


def test_it_fills_at_the_limit_on_a_later_tick_that_reaches_it(conn):
    cfg = config("limit-005", 0.05)
    tick(conn, cfg, "limit-005", 0)
    tick(conn, cfg, "limit-005", 60)  # mid 2.40 still under 2.45: working
    assert positions(conn, "limit-005") == []
    tick(conn, cfg, "limit-005", 90, puts=RICHER)  # mid 2.50
    (pos,) = positions(conn, "limit-005")
    assert pos["credit"] == pytest.approx(2.45) and pos["slippage_dollars"] == 0.0
    assert pos["entry_time"].startswith("2026-07-20T12:01:30")
    (order,) = orders(conn)
    assert order["status"] == "filled" and order["position_id"] == pos["position_id"]


def test_the_placing_tick_never_fills_its_own_order(conn):
    """At offset 0 the limit sits at or under mid on the tick it is placed; it still waits for a
    later tick, or the arm would be control without its slippage."""
    cfg = config("limit-000", 0.0)
    tick(conn, cfg, "limit-000", 0)
    assert positions(conn, "limit-000") == []
    tick(conn, cfg, "limit-000", 60)
    assert [r["credit"] for r in positions(conn, "limit-000")] == [pytest.approx(2.40)]


def test_an_unfilled_order_lapses_and_the_entry_is_planned_again(conn):
    cfg = config("limit-010", 0.10, window=120)
    tick(conn, cfg, "limit-010", 0)
    tick(conn, cfg, "limit-010", 60)
    assert [o["status"] for o in orders(conn)] == ["resting"]
    tick(conn, cfg, "limit-010", 120)
    assert [o["status"] for o in orders(conn)] == ["expired", "resting"]
    assert positions(conn, "limit-010") == []


def test_the_limit_arms_are_registered():
    for arm in ("limit-000", "limit-005", "limit-010", "limit-000-rh", "limit-005-rh", "limit-010-rh"):
        assert arm in engine.ARMS
