"""Half days (an NYSE early close, 13:00 ET) are not tradeable: the ladder takes no new lot, paper or
live, all day, extended hours included. What it already holds is still marked and sold.

2026-11-27, the day after Thanksgiving, was one. Each guard here is shown to fail with it removed."""

from conftest import at
from test_live_gates import by_name, gates
from test_orders import FakeBroker, live_block, lot
from test_orders import at as order_at
from test_orders import run as plan_run

from cherrypick.ladder import db, live_gates, paper_loop

HALF = "2026-11-27"  # the day after Thanksgiving: closes at 13:00
ORDINARY = "2026-11-20"  # a Friday that closes at 16:00


def _once(config):
    return {**config, "arms": {"once": config["arms"]["once"]}}


def _tick(config, ledger, cache, day, hhmm="10:00"):
    return paper_loop.run_once(config, ledger, cache_path=cache.path, when=at(day, hhmm))


def _decisions(ledger, day):
    return ledger.execute(
        "SELECT arm, symbol, mode, reason, accepted FROM ladder_decisions WHERE trade_date = ?", (day,)
    ).fetchall()


# --------------------------------------------------------------------------- paper
def test_paper_buys_on_an_ordinary_day_at_the_level(config, ledger, cache):
    cache.summary("TQQQ", ORDINARY, prev_day_close=100.0).quote("TQQQ", 92.98, 93.00)
    _tick(_once(config), ledger, cache, ORDINARY)
    assert len(db.open_lots(ledger)) == 1


def test_paper_takes_no_new_lot_on_a_half_day_and_says_why(config, ledger, cache):
    cache.summary("TQQQ", HALF, prev_day_close=100.0).quote("TQQQ", 92.98, 93.00)
    out = _tick(_once(config), ledger, cache, HALF)
    assert db.open_lots(ledger) == [] and out["actions"] == 0
    rows = _decisions(ledger, HALF)
    assert [(r["mode"], r["reason"], r["accepted"]) for r in rows] == [
        ("entry", paper_loop.HALF_DAY_REASON, 0)
    ]
    assert paper_loop.HALF_DAY_REASON == "half_day_no_entries"


def test_paper_still_sells_a_held_lot_on_a_half_day(config, ledger, cache):
    cfg = _once(config)
    cache.summary("TQQQ", ORDINARY, prev_day_close=100.0).quote("TQQQ", 92.98, 93.00)
    _tick(cfg, ledger, cache, ORDINARY)
    assert len(db.open_lots(ledger)) == 1  # bought at 93.00, target 96.72
    cache.quote("TQQQ", 97.00, 97.02)
    out = _tick(cfg, ledger, cache, HALF)
    assert db.open_lots(ledger) == [] and out["by_arm"]["once:TQQQ"] == {"sold": 1, "bought": 0}


def test_force_still_trades_a_half_day_for_testing(config, ledger, cache):
    cache.summary("TQQQ", HALF, prev_day_close=100.0).quote("TQQQ", 92.98, 93.00)
    paper_loop.run_once(_once(config), ledger, cache_path=cache.path, when=at(HALF, "10:00"), force=True)
    assert len(db.open_lots(ledger)) == 1


# --------------------------------------------------------------------------- live
def test_the_live_planner_places_no_buy_on_a_half_day_and_cancels_the_resting_ones():
    broker = FakeBroker()
    plan_run(broker=broker, live=live_block(max_open_lots=1))  # an ordinary day: one buy rests
    assert [w["spec"]["legs"][0]["action"] for w in broker.working] == ["buy to open"]

    p, _ = plan_run(
        broker=broker,
        when=order_at("10:00", HALF),
        lots=[lot(shares=10, cost=93.0)],
        marks={"TQQQ": 93.0},
    )
    assert "half_day_no_entries" in p.buys_blocked
    assert [a.side for a in p.places] == ["sell"] and [a.side for a in p.cancels] == ["buy"]
    assert [w["spec"]["legs"][0]["action"] for w in broker.working] == ["sell to close"]


def test_the_live_planner_places_buys_on_an_ordinary_day():
    p, _ = plan_run(when=order_at("10:00", ORDINARY), live=live_block(max_open_lots=1))
    assert [a.side for a in p.places] == ["buy"] and "half_day_no_entries" not in p.buys_blocked


def test_a_half_day_is_a_new_buys_gate_not_a_stop_on_everything():
    on_half = by_name(gates(today=HALF, armed_for=HALF))["session.half_day"]
    assert on_half.ok is False and on_half.blocks == "new_buys"
    assert "half days are not tradeable" in on_half.reason
    assert live_gates.ready(gates(today=HALF, armed_for=HALF)) is True  # held lots are still managed
    assert live_gates.new_buys_allowed(gates(today=HALF, armed_for=HALF)) is False

    ordinary = by_name(gates(today=ORDINARY, armed_for=ORDINARY))["session.half_day"]
    assert ordinary.ok is True
    assert live_gates.new_buys_allowed(gates(today=ORDINARY, armed_for=ORDINARY)) is True
