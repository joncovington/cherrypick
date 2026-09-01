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
from cherrypick.earnings import strategy_metrics as metrics

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
    """An isolated book, state directory, and quote source — no broker, no streamer, no real home.

    strategy_metrics keeps its OWN module-level DB_PATH, so patching db_paper's alone leaves the EOD
    writer reading the developer's real paper book. That passes locally and fails in CI, which is the
    wrong way round: a test that silently reads live data is worse than one that fails.
    """
    book = tmp_path / "paper_trades.db"
    monkeypatch.setattr(db_paper, "DB_PATH", book)
    monkeypatch.setattr(metrics, "DB_PATH", book)
    monkeypatch.setattr(metrics, "PAPER_DB_PATH", book)
    monkeypatch.setattr(harness, "_eod_report_path", lambda day: tmp_path / f"paper-eod-{day}.md")
    monkeypatch.setattr(harness, "_analysis_path", lambda day: tmp_path / f"eod-analysis-{day}.md")
    db_paper.cmd_init_db(argparse.Namespace())
    monkeypatch.setattr(paper_loop, "state_file", lambda name: tmp_path / name)
    monkeypatch.setattr(paper_loop, "lock_path", lambda: tmp_path / "loop.lock")
    monkeypatch.setattr(paper_loop.stream_request, "register", lambda *a, **k: None)
    # Costs are exercised by their own suite; here they must simply not reach a broker.
    monkeypatch.setattr(
        paper_loop.costs, "apply_exit_costs", lambda *a, **k: {"total_cost": 1.0, "slippage": 0.1}
    )
    return tmp_path


def open_trade(order_id="T1", credit=5.00, opened_at=None, expiration="2026-08-21", profile="strat_test:iron_fly"):
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
                    "profile": profile,
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
                # A wide stub carries a genuinely wide leg (percent AND money); the default stays
                # zero-width so priced closes stay exact. Widening the second short shifts the exit
                # debit, which only the blocked-exit test uses this for -- and there no close prices.
                LEGS[1]["symbol"]: {"bid": 0.0, "ask": spread if spread > 0.25 else 0.0, "mid": 0.01},
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


def test_an_advised_twin_is_managed_beside_its_control(priced):
    """The twin is a different book (`advised:strat_test:<strategy>`), and the loop must still see it.

    It did not, for six days: `open_positions` filtered on `_is_strat_test_book`, which answers "is
    this a strat_test book" -- the right question for `run_closes` and the wrong one here -- so every
    advised twin ever opened was never marked, never evaluated and never closed. 13 of them, against
    4,953 marks on the controls beside them, and the advised experiment recorded nothing at all.

    The failure was invisible from the design: `management.effective_config` really is the one choke
    point restating a twin's frozen params, and it really is reached from `management.evaluate`. But
    `evaluate` only ever sees what `open_positions` returns.

    Verified by restoring the bare `_is_strat_test_book` call and watching the twin go unmarked while
    its control closed normally.
    """
    control = open_trade("T1")
    twin = open_trade("T2", profile="advised:strat_test:iron_fly")
    priced(quotes_pricing(3.00))  # past the 25% target for both
    paper_loop.run_iteration(CONFIG, at("10:00"))

    row = db_paper.cmd_get_iterations(_ns(session_date=None, limit=None))["iterations"][0]
    assert row["open_positions"] == 2 and row["marks_written"] == 2

    for order_id in (control, twin):
        events = db_paper.cmd_get_management_events(
            _ns(order_id=order_id, session_date=None, limit=None)
        )["events"]
        assert events, f"{order_id} was never evaluated"
        assert events[0]["reason"] == "profit_target"
    assert not db_paper.cmd_get_open_positions(_ns())["positions"], "both should have closed"


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
    # 2.10 + the wide leg's 0.90 ask prices the close at 3.00 -- past the 25% target, so the
    # decision fires and only the gate stands between it and execution.
    priced(quotes_pricing(2.10, spread=0.90))
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


def test_the_entry_scan_declares_what_it_just_opened(monkeypatch):
    """Changed 2026-08-25, and the rule it replaces was right for its time.

    Declaring here used to grow the `symbols` union, which a producer binds once at startup -- so
    the watchdog recycled it, and a recycle costs a settling window in which nothing streams at all.
    At 15:45 that blinded the 0DTE modules trading into their own close, to make symbols available
    fourteen hours before this module marked anything, so `pre_open` picked them up instead.

    They are quote-only `legs` now, re-read every subscription poll rather than bound at startup, so
    this module cannot force a recycle at all. The cost of waiting is what remains: a position
    opened tonight has no underlying spot until the next pre-open, which silently disables the pin
    guard for every mark in between.
    """
    registered = []
    monkeypatch.setattr(paper_loop.stream_request, "register", lambda syms: registered.append(syms))
    monkeypatch.setattr(harness, "cmd_run_entries", lambda args: {"ok": True, "opened": 3})
    open_trade()

    paper_loop.run_iteration(CONFIG, at("15:45"))
    assert registered == [["AAPL"]], "the entry declares its own underlyings now"

    paper_loop.run_iteration(CONFIG, at("09:05"))
    assert registered[-1] == ["AAPL"], "pre_open still declares, so a restart cannot lose them"


def test_closing_a_position_still_refreshes_the_request(priced, monkeypatch):
    """Shrinking is always safe: an over-subscribed producer serves everyone correctly and the
    growth-only staleness check never recycles for it."""
    registered = []
    monkeypatch.setattr(paper_loop.stream_request, "register", lambda syms: registered.append(syms))
    open_trade()
    priced(quotes_pricing(3.00))
    paper_loop.run_iteration(CONFIG, at("10:00"))

    assert registered == [[]], "the position closed, so its underlying is no longer needed"


def test_append_run_log_restores_the_per_run_trail(tmp_path, monkeypatch):
    """The heartbeats are a LATEST -- overwritten every run, so they answer "is it alive" and
    nothing else. When entry and exit moved into this loop at the 2026-08-12 cutover, nothing took
    over the run trail: logs/earnings_paper.log simply stopped, and "did earnings run today, and
    what did it decide" stopped being answerable from the logs while the loop ran fine.
    """
    import json as _json

    from cherrypick.core import home as _home

    from cherrypick.earnings import paper_loop as pl

    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    monkeypatch.setattr(_home, "logs_dir", lambda: tmp_path / "logs")

    pl.append_run_log(
        {"ts": "2026-08-13T19:37:56+00:00", "date": "2026-08-13", "phase": "entry", "opened": []}
    )
    pl.append_run_log(
        {"ts": "2026-08-13T19:38:56+00:00", "date": "2026-08-13", "phase": "exit", "closed": []}
    )

    lines = (tmp_path / "logs" / "earnings_paper.log").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2, "one object per line, appended -- not overwritten like the heartbeat"
    assert [_json.loads(x)["phase"] for x in lines] == ["entry", "exit"]


def test_append_run_log_never_breaks_a_session(tmp_path, monkeypatch):
    """A session that cannot write its log still trades."""
    from cherrypick.core import home as _home

    from cherrypick.earnings import paper_loop as pl

    monkeypatch.setattr(_home, "logs_dir", lambda: tmp_path / "nope" / "deeper")
    (tmp_path / "nope").write_text("not a directory", encoding="utf-8")
    pl.append_run_log({"phase": "entry"})  # must not raise
