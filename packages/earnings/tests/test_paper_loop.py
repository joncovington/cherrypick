"""The managed loop: what a tick does, and what it refuses to do.

Driven against a fake clock and a stubbed quote source, so a whole session can be stepped through
without a broker or a streamer. The properties worth pinning are the ones that only show up across
ticks — the entry scan running once, a decision seen early being taken later, a held winner
surviving a session boundary — plus the ones that keep the loop safe to run every 60 seconds.
"""

import argparse
import json
from datetime import datetime

import pytest

from cherrypick.earnings import db_paper, paper_loop
from cherrypick.earnings import strat_test_harness as harness

ET = paper_loop.ET

LEGS = [
    {
        "symbol": "AAPL  260821C00190000",
        "streamer_symbol": ".AAPL260821C190",
        "action": "Sell to Open",
        "quantity": 1,
    },
    {
        "symbol": "AAPL  260821P00190000",
        "streamer_symbol": ".AAPL260821P190",
        "action": "Sell to Open",
        "quantity": 1,
    },
]

CONFIG = {
    "strategies": {"iron_fly": {"profit_target_pct": 0.25, "stop_loss_credit_multiple": 1.5}},
    "management": {},
    "tastytrade_costs": {},
}


def at(hhmm, day="2026-08-12"):
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00").replace(tzinfo=ET)


def _ns(**kwargs):
    return argparse.Namespace(**kwargs)


@pytest.fixture(autouse=True)
def wired(tmp_path, monkeypatch):
    """An isolated book, state directory, and quote source — no broker, no streamer, no real home."""
    monkeypatch.setattr(db_paper, "DB_PATH", tmp_path / "paper_trades.db")
    db_paper.cmd_init_db(argparse.Namespace())
    monkeypatch.setattr(paper_loop, "state_file", lambda name: tmp_path / name)
    monkeypatch.setattr(paper_loop, "lock_path", lambda: tmp_path / "loop.lock")
    monkeypatch.setattr(paper_loop.stream_request, "register", lambda *a, **k: None)
    # Costs are exercised by their own suite; here they must simply not reach a broker.
    monkeypatch.setattr(
        paper_loop.costs, "apply_exit_costs", lambda *a, **k: {"total_cost": 1.0, "slippage": 0.1}
    )
    return tmp_path


def open_trade(order_id="T1", credit=5.00, opened_at=None, expiration="2026-08-21"):
    db_paper.cmd_save_trade(
        _ns(
            data=json.dumps(
                {
                    "order_id": order_id,
                    "symbol": "AAPL",
                    "strategy": "iron_fly",
                    "expiration": expiration,
                    "entry_credit": credit,
                    "legs_json": json.dumps(LEGS),
                    "profile": "strat_test:iron_fly",
                    "quantity": 1,
                    "capital_at_risk": 500.0,
                    "opened_at": opened_at
                    if opened_at is not None
                    else at("15:45", "2026-08-11").timestamp(),
                }
            )
        )
    )
    return order_id


def quotes_pricing(exit_debit, *, spread=0.05):
    """Every tick prices the structure to exactly `exit_debit`."""

    def stub(trade, **kwargs):
        return {
            "ok": True,
            "source": "stream",
            "spot": 190.0,
            "fresh": 2,
            "stale": 0,
            "max_spread_pct": spread,
            "quotes": {
                LEGS[0]["symbol"]: {"bid": exit_debit, "ask": exit_debit, "mid": max(exit_debit, 0.01)},
                LEGS[1]["symbol"]: {"bid": 0.0, "ask": 0.0, "mid": 0.01},
            },
        }

    return stub


def refusing(reason="missing_leg_quotes"):
    def stub(trade, **kwargs):
        return {"ok": False, "reason": reason}

    return stub


@pytest.fixture
def priced(monkeypatch):
    """Point both the cache and the broker at the same stubbed price."""

    def use(stub):
        monkeypatch.setattr(paper_loop.provider, "snapshot", stub)
        monkeypatch.setattr(paper_loop, "_rest_snapshot", lambda trade: stub(trade))

    return use


# --------------------------------------------------------------------------- phases
def test_a_weekend_tick_does_nothing_and_records_nothing():
    """An out-of-session tick is not a measurement; a row for one would make the iteration table
    mostly noise and hide the stretch that matters."""
    result = paper_loop.run_iteration(CONFIG, at("10:00", "2026-08-15"))
    assert result["phase"] == "off_hours"
    assert db_paper.cmd_get_iterations(_ns(session_date=None, limit=None))["iterations"] == []


def test_a_management_tick_records_its_vital_signs(priced):
    open_trade()
    priced(quotes_pricing(4.50))
    paper_loop.run_iteration(CONFIG, at("10:00"))

    row = db_paper.cmd_get_iterations(_ns(session_date=None, limit=None))["iterations"][0]
    assert row["phase"] == "management" and row["status"] == "ok"
    assert row["open_positions"] == 1 and row["marks_written"] == 1
    assert row["open_capital"] == 500.0


# --------------------------------------------------------------------------- marking and gating
def test_the_open_window_marks_but_never_acts(priced):
    """The first ten minutes of an earnings name's options are not priceable. The target is seen,
    recorded, and taken later — not lost, and not acted on now."""
    order_id = open_trade()
    priced(quotes_pricing(3.00))  # well past the 25% target
    paper_loop.run_iteration(CONFIG, at("09:35"))

    assert db_paper.cmd_get_open_positions(_ns())["positions"], "must not have closed"
    event = db_paper.cmd_get_management_events(_ns(order_id=order_id, session_date=None, limit=None))[
        "events"
    ][0]
    assert event["reason"] == "profit_target" and event["executed"] == 0
    assert event["gate"] in ("before_exec_window", "open_window")


def test_a_target_seen_early_is_taken_on_the_first_tick_that_clears(priced):
    """The pair that makes a 09:41 exit on a 09:33 target explicable rather than looking late."""
    order_id = open_trade()
    priced(quotes_pricing(3.00))
    paper_loop.run_iteration(CONFIG, at("09:35"))
    paper_loop.run_iteration(CONFIG, at("09:41"))

    assert db_paper.cmd_get_open_positions(_ns())["positions"] == []
    events = db_paper.cmd_get_management_events(_ns(order_id=order_id, session_date=None, limit=None))[
        "events"
    ]
    assert [e["executed"] for e in events] == [1, 0]  # newest first: taken, then the gated one


def test_a_refused_mark_is_recorded_and_the_position_is_left_alone(priced, monkeypatch):
    order_id = open_trade()
    monkeypatch.setattr(paper_loop.provider, "snapshot", refusing())
    monkeypatch.setattr(
        paper_loop, "_rest_snapshot", lambda trade: {"ok": False, "reason": "missing_leg_quotes"}
    )
    paper_loop.run_iteration(CONFIG, at("10:00"))

    mark = db_paper.cmd_get_marks(_ns(order_id=order_id, session_date=None, limit=None))["marks"][0]
    assert mark["usable"] == 0 and mark["refusal"] == "missing_leg_quotes"
    assert db_paper.cmd_get_open_positions(_ns())["positions"], "an unpriceable position is not closed"


def test_the_cache_is_tried_before_the_broker(monkeypatch):
    """Streamer first: the broker costs a subprocess and a fresh session per call, and this runs
    every minute."""
    open_trade()
    calls = []
    monkeypatch.setattr(
        paper_loop.provider, "snapshot", lambda t, **k: calls.append("cache") or quotes_pricing(4.5)(t)
    )
    monkeypatch.setattr(
        paper_loop, "_rest_snapshot", lambda t: calls.append("rest") or quotes_pricing(4.5)(t)
    )
    paper_loop.run_iteration(CONFIG, at("10:00"))

    assert calls == ["cache"], "a usable cached mark must not reach the broker"


def test_the_broker_confirms_the_price_before_a_close_is_recorded(monkeypatch):
    """The decision is the cache's; the price on the ledger should be one we could have traded."""
    open_trade()
    monkeypatch.setattr(paper_loop.provider, "snapshot", quotes_pricing(3.00))
    monkeypatch.setattr(paper_loop, "_rest_snapshot", quotes_pricing(3.20))
    paper_loop.run_iteration(CONFIG, at("10:00"))

    closed = db_paper.cmd_get_pnl_summary(_ns(strategy=None, profile=None))["trades"][0]
    assert closed["exit_debit"] == 3.20, "the confirmed price, not the cached one"


def test_wide_quotes_are_marked_but_not_acted_on(priced):
    order_id = open_trade()
    priced(quotes_pricing(3.00, spread=0.90))
    paper_loop.run_iteration(CONFIG, at("10:00"))

    assert db_paper.cmd_get_open_positions(_ns())["positions"]
    event = db_paper.cmd_get_management_events(_ns(order_id=order_id, session_date=None, limit=None))[
        "events"
    ][0]
    assert event["gate"] == "spread_too_wide"


# --------------------------------------------------------------------------- the lifecycle rules
def test_a_winner_short_of_target_survives_the_first_morning(priced):
    """The change from the old sweep, end to end: this position used to close at 09:45."""
    open_trade()
    priced(quotes_pricing(4.50))
    paper_loop.run_iteration(CONFIG, at("09:45"))

    assert len(db_paper.cmd_get_open_positions(_ns())["positions"]) == 1


def test_a_loser_is_closed_on_the_first_morning(priced):
    open_trade()
    priced(quotes_pricing(6.00))
    paper_loop.run_iteration(CONFIG, at("10:00"))

    closed = db_paper.cmd_get_pnl_summary(_ns(strategy=None, profile=None))["trades"][0]
    assert closed["exit_reason"] == "pead_loser"


def test_a_held_winner_survives_into_the_next_session(priced):
    """The first-check-of-day flag is derived from the marks table, so it resets across a session
    boundary without the loop holding any state between ticks."""
    order_id = open_trade()
    priced(quotes_pricing(4.50))
    paper_loop.run_iteration(CONFIG, at("10:00", "2026-08-12"))
    paper_loop.run_iteration(CONFIG, at("10:00", "2026-08-13"))

    assert db_paper.cmd_get_open_positions(_ns())["positions"]
    sessions = {
        m["session_date"]
        for m in db_paper.cmd_get_marks(_ns(order_id=order_id, session_date=None, limit=None))["marks"]
    }
    assert sessions == {"2026-08-12", "2026-08-13"}


def test_the_session_cap_closes_a_carried_winner(priced):
    open_trade(opened_at=at("15:45", "2026-08-07").timestamp())
    priced(quotes_pricing(4.50))
    paper_loop.run_iteration(CONFIG, at("10:00", "2026-08-12"))

    closed = db_paper.cmd_get_pnl_summary(_ns(strategy=None, profile=None))["trades"][0]
    assert closed["exit_reason"] == "max_hold"
    assert closed["hold_days"] >= 3


def test_the_excursion_is_recorded_across_ticks(priced, monkeypatch):
    order_id = open_trade()
    for debit in (4.50, 5.50, 4.80):
        monkeypatch.setattr(paper_loop.provider, "snapshot", quotes_pricing(debit))
        monkeypatch.setattr(paper_loop, "_rest_snapshot", quotes_pricing(debit))
        paper_loop.run_iteration(CONFIG, at("11:00"))

    row = [p for p in db_paper.cmd_get_open_positions(_ns())["positions"] if p["order_id"] == order_id][0]
    assert row["max_unrealized_pnl"] == pytest.approx(50.0)
    assert row["min_unrealized_pnl"] == pytest.approx(-50.0)


def test_the_tick_execution_cap_defers_the_rest_to_the_next_tick(priced):
    """A first-morning burst of closes each costs a broker round trip; capping keeps one tick
    bounded, and the deferred position is closed a minute later rather than dropped."""
    for i in range(5):
        open_trade(order_id=f"T{i}")
    priced(quotes_pricing(3.00))
    paper_loop.run_iteration(CONFIG, at("10:00"))

    assert len(db_paper.cmd_get_open_positions(_ns())["positions"]) == 2
    paper_loop.run_iteration(CONFIG, at("10:01"))
    assert db_paper.cmd_get_open_positions(_ns())["positions"] == []


# --------------------------------------------------------------------------- entry, EOD, locking
def test_the_entry_scan_runs_once_a_day(monkeypatch):
    calls = []
    monkeypatch.setattr(
        harness, "cmd_run_entries", lambda args: calls.append(args.date) or {"ok": True, "opened": 2}
    )

    first = paper_loop.run_iteration(CONFIG, at("15:45"))
    second = paper_loop.run_iteration(CONFIG, at("15:50"))

    assert first["phase"] == "entry" and len(calls) == 1
    assert second["phase"] != "entry", "the heartbeat's date is what makes the scan idempotent"


def test_the_entry_scan_writes_the_sla_heartbeat_the_watchdog_reads(monkeypatch, wired):
    monkeypatch.setattr(harness, "cmd_run_entries", lambda args: {"ok": True, "opened": 2})
    paper_loop.run_iteration(CONFIG, at("15:45"))

    record = json.loads((wired / "earnings_entry.last.json").read_text(encoding="utf-8"))
    assert record["date"] == "2026-08-12" and record["ok"] is True


def test_the_eod_reports_are_written_before_the_digest_deadline(monkeypatch):
    """The suite digest fires once every module has written its paper-eod file, with a 16:45
    backstop. Writing at 16:00-16:30 keeps earnings inside it."""
    written = []
    monkeypatch.setattr(harness, "_write_eod_report", lambda day: written.append(("report", day)))
    monkeypatch.setattr(harness, "_write_eod_analysis", lambda day: written.append(("analysis", day)))

    result = paper_loop.run_iteration(CONFIG, at("16:05"))
    assert result["phase"] == "eod"
    assert written == [("report", "2026-08-12"), ("analysis", "2026-08-12")]


def test_a_busy_tick_reports_ok_rather_than_failing(wired):
    """The entry scan holds the lock for up to twenty-five minutes. A supervisor logging that as
    failure for twenty-five ticks teaches everyone to ignore the log."""
    held = paper_loop.acquire_lock()
    try:
        result = paper_loop.cmd_once(_ns())
        assert result["ok"] is True and result["status"] == "busy"
    finally:
        paper_loop.release_lock(held)


def test_a_stale_lock_does_not_wedge_the_loop(wired, monkeypatch):
    lock = paper_loop.lock_path()
    lock.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(paper_loop, "LOCK_STALE_SECONDS", -1)
    assert paper_loop.acquire_lock().acquired is True


def test_the_lock_is_released_even_when_a_tick_raises(wired, monkeypatch):
    monkeypatch.setattr(paper_loop, "run_iteration", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        paper_loop.cmd_once(_ns())
    assert not paper_loop.lock_path().exists()


def test_status_reports_the_phase_without_touching_a_broker(monkeypatch):
    monkeypatch.setattr(paper_loop.scanner, "_load_config", lambda *a, **k: CONFIG)
    result = paper_loop.cmd_status(_ns())
    assert result["ok"] and "phase" in result and result["open_positions"] == 0


def test_the_same_session_backstop_cannot_close_a_held_winner(priced):
    """End-to-end guard on the interaction that would have silently defeated multi-day holds: the
    strategies' 240-minute backstop, left in force, fires on every position the first morning."""
    open_trade(opened_at=at("15:45", "2026-08-11").timestamp())
    priced(quotes_pricing(4.50))
    paper_loop.run_iteration(CONFIG, at("10:00", "2026-08-12"))

    assert db_paper.cmd_get_open_positions(_ns())["positions"], "18 hours old, and still working"


def test_the_entry_scan_never_starts_after_the_entry_window(monkeypatch):
    """Without a deadline, any later tick that found no entry heartbeat would run the scan — opening
    positions after the close, on a chain nobody can trade, and reporting them as that day's
    entries. A missed scan is a missed entry, which the SLA check already reports as one."""
    calls = []
    monkeypatch.setattr(harness, "cmd_run_entries", lambda args: calls.append(args.date) or {"ok": True})

    assert paper_loop.run_iteration(CONFIG, at("16:05"))["phase"] != "entry"
    assert paper_loop.run_iteration(CONFIG, at("21:00"))["phase"] == "off_hours"
    assert calls == []


def test_the_entry_scan_still_runs_inside_the_window(monkeypatch):
    monkeypatch.setattr(harness, "cmd_run_entries", lambda args: {"ok": True, "opened": 1})
    assert paper_loop.run_iteration(CONFIG, at("15:54"))["phase"] == "entry"
