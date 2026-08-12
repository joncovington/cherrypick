"""The lifecycle half of the paper schema: marks, management events, loop iterations, the
producer's leg set, and measurement breaks.

Positions are managed between entry and exit now rather than force-closed the next morning, so the
properties worth asserting are about the PATH being recoverable afterwards:
  - a refused mark is still recorded, so a stalled feed and a quiet market never look alike,
  - a decision an execution gate held back is still recorded, so a late exit is explicable,
  - only a usable mark moves the excursion, so a dead feed cannot invent a drawdown,
  - hold length counts trading sessions, so a weekend cannot spend a hold budget,
  - a database created before any of this existed migrates without relabelling its history.
"""

import argparse
import json
import sqlite3
import time
from datetime import date, timedelta

import pytest

from cherrypick.earnings import db_paper


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_paper, "DB_PATH", tmp_path / "paper_trades.db")
    db_paper.cmd_init_db(argparse.Namespace())


def _ns(**kwargs):
    return argparse.Namespace(**kwargs)


def _save(order_id="P1", *, opened_at=None, capital_at_risk=None):
    db_paper.cmd_save_trade(
        _ns(
            data=json.dumps(
                {
                    "order_id": order_id,
                    "symbol": "AAPL",
                    "strategy": "iron_fly",
                    "expiration": "2026-08-21",
                    "entry_credit": 2.0,
                    "legs_json": "[]",
                    "opened_at": opened_at if opened_at is not None else time.time(),
                    "capital_at_risk": capital_at_risk,
                }
            )
        )
    )
    return order_id


def _mark(order_id, **spec):
    return db_paper.cmd_record_mark(_ns(data=json.dumps({"order_id": order_id, **spec})))


# --------------------------------------------------------------------------- marks
def test_a_new_trade_starts_open():
    _save()
    assert db_paper.cmd_get_open_positions(_ns())["positions"][0]["status"] == "open"


def test_a_usable_mark_records_price_and_feed_health():
    _save()
    result = _mark(
        "P1",
        exit_debit=1.2,
        unrealized_pnl=80.0,
        spot=190.5,
        source="stream",
        usable=True,
        quotes_fresh=4,
        quotes_stale=0,
        max_leg_spread_pct=0.06,
    )
    assert result["ok"] and result["mark_id"]

    mark = db_paper.cmd_get_marks(_ns(order_id="P1", session_date=None, limit=None))["marks"][0]
    assert mark["exit_debit"] == 1.2 and mark["source"] == "stream"
    assert mark["usable"] == 1 and mark["quotes_fresh"] == 4


def test_a_refused_mark_is_still_recorded():
    """The measurement is "we looked and could not price it". Dropping it would make a stalled feed
    indistinguishable from a market in which nothing happened."""
    _save()
    _mark("P1", usable=False, refusal="no_fresh_quotes", quotes_stale=4)

    mark = db_paper.cmd_get_marks(_ns(order_id="P1", session_date=None, limit=None))["marks"][0]
    assert mark["usable"] == 0 and mark["refusal"] == "no_fresh_quotes"


def test_only_a_usable_mark_moves_the_excursion():
    """A refused mark carries no price worth the name; letting one set a new low would invent a
    drawdown the position never had."""
    _save()
    _mark("P1", unrealized_pnl=50.0, usable=True)
    _mark("P1", unrealized_pnl=-30.0, usable=True)
    _mark("P1", unrealized_pnl=-9999.0, usable=False, refusal="no_fresh_quotes")

    row = db_paper.cmd_get_open_positions(_ns())["positions"][0]
    assert row["max_unrealized_pnl"] == 50.0
    assert row["min_unrealized_pnl"] == -30.0


def test_the_first_usable_mark_seeds_both_excursion_ends():
    _save()
    _mark("P1", unrealized_pnl=12.0, usable=True)
    row = db_paper.cmd_get_open_positions(_ns())["positions"][0]
    assert row["max_unrealized_pnl"] == 12.0 and row["min_unrealized_pnl"] == 12.0


def test_marks_are_scoped_by_session():
    _save("A")
    _save("B")
    _mark("A", usable=True, session_date="2026-08-12")
    _mark("B", usable=True, session_date="2026-08-13")

    today = db_paper.cmd_get_marks(_ns(order_id=None, session_date="2026-08-12", limit=None))
    assert [m["order_id"] for m in today["marks"]] == ["A"]


# --------------------------------------------------------------------------- management events
def test_an_unexecuted_decision_records_the_gate_that_held_it():
    """The most interesting row in the table: the only record that the system saw the exit before
    it was allowed to take it, which is what makes a 09:41 exit on a 09:33 target explicable."""
    _save()
    db_paper.cmd_record_management_event(
        _ns(
            data=json.dumps(
                {
                    "order_id": "P1",
                    "action": "close_all",
                    "reason": "profit_target",
                    "executed": False,
                    "gate": "before_exec_window",
                    "phase": "open_window",
                }
            )
        )
    )
    event = db_paper.cmd_get_management_events(_ns(order_id="P1", session_date=None, limit=None))["events"][0]
    assert event["executed"] == 0 and event["gate"] == "before_exec_window"
    assert event["reason"] == "profit_target"


def test_a_management_event_requires_an_action_and_a_reason():
    """An event with no reason is a row nobody can interpret later."""
    result = db_paper.cmd_record_management_event(_ns(data=json.dumps({"order_id": "P1", "action": "hold"})))
    assert result["ok"] is False


def test_event_detail_round_trips_as_json():
    _save()
    db_paper.cmd_record_management_event(
        _ns(
            data=json.dumps(
                {
                    "order_id": "P1",
                    "action": "close_all",
                    "reason": "stop_loss",
                    "executed": True,
                    "detail": {"exit_debit": 4.0, "threshold": 3.0},
                }
            )
        )
    )
    event = db_paper.cmd_get_management_events(_ns(order_id="P1", session_date=None, limit=None))["events"][0]
    assert json.loads(event["detail_json"])["threshold"] == 3.0


# --------------------------------------------------------------------------- loop iterations
def test_an_iteration_records_the_loops_vital_signs():
    db_paper.cmd_record_iteration(
        _ns(
            data=json.dumps(
                {
                    "phase": "management",
                    "status": "ok",
                    "open_positions": 3,
                    "marks_written": 3,
                    "actions_taken": 1,
                    "open_capital": 2482.0,
                    "duration_ms": 1400,
                }
            )
        )
    )
    row = db_paper.cmd_get_iterations(_ns(session_date=None, limit=None))["iterations"][0]
    assert row["status"] == "ok" and row["open_positions"] == 3 and row["open_capital"] == 2482.0


def test_a_refused_iteration_names_its_reason():
    """'ok' or the refusal — a live-but-quiet loop must be distinguishable from a dead one without
    reading logs, which is the whole reason this table exists."""
    db_paper.cmd_record_iteration(
        _ns(data=json.dumps({"phase": "management", "status": "stream_cache_stale"}))
    )
    assert (
        db_paper.cmd_get_iterations(_ns(session_date=None, limit=None))["iterations"][0]["status"]
        == "stream_cache_stale"
    )


# --------------------------------------------------------------------------- producer leg set
def test_open_leg_symbols_are_replaced_not_merged():
    """The legs of a position are known in full at entry; merging would leave a symbol from a
    corrected order subscribed forever."""
    _save()
    db_paper.cmd_set_open_legs(_ns(data=json.dumps({"order_id": "P1", "streamer_symbols": [".A", ".B"]})))
    db_paper.cmd_set_open_legs(_ns(data=json.dumps({"order_id": "P1", "streamer_symbols": [".C"]})))

    conn = sqlite3.connect(db_paper.DB_PATH)
    try:
        rows = [r[0] for r in conn.execute("SELECT streamer_symbol FROM open_leg_symbols").fetchall()]
    finally:
        conn.close()
    assert rows == [".C"]


def test_clearing_leg_symbols_stops_the_producer_holding_them():
    _save()
    db_paper.cmd_set_open_legs(_ns(data=json.dumps({"order_id": "P1", "streamer_symbols": [".A", ".B"]})))
    assert db_paper.cmd_clear_open_legs(_ns(order_id="P1"))["removed"] == 2


def test_blank_leg_symbols_are_dropped_not_stored():
    _save()
    result = db_paper.cmd_set_open_legs(
        _ns(data=json.dumps({"order_id": "P1", "streamer_symbols": [".A", "", "  ", None]}))
    )
    assert result["count"] == 1


# --------------------------------------------------------------------------- hold length
def test_the_standard_overnight_hold_is_one_session():
    monday = date(2026, 8, 10)
    tuesday = monday + timedelta(days=1)
    span = db_paper.session_span(
        time.mktime(monday.timetuple()) + 15.75 * 3600,
        time.mktime(tuesday.timetuple()) + 9.75 * 3600,
    )
    assert span == 1


def test_a_weekend_does_not_spend_the_hold_budget():
    """A Friday entry closed Monday has been held one session, not three — a three-session cap
    must not be two thirds consumed by a weekend."""
    friday = date(2026, 8, 14)
    monday = friday + timedelta(days=3)
    assert friday.weekday() == 4 and monday.weekday() == 0
    span = db_paper.session_span(
        time.mktime(friday.timetuple()) + 15.75 * 3600,
        time.mktime(monday.timetuple()) + 9.75 * 3600,
    )
    assert span == 1


def test_a_same_day_close_is_zero_sessions():
    noon = time.mktime(date(2026, 8, 10).timetuple()) + 12 * 3600
    assert db_paper.session_span(noon, noon + 3600) == 0


def test_an_unusable_timestamp_is_unknown_not_zero():
    """Callers gate holds on this; a None that silently became 0 would read as 'just opened'."""
    assert db_paper.session_span(None, time.time()) is None
    assert db_paper.session_span("not a time", time.time()) is None


def test_closing_records_the_reason_and_the_hold_length():
    monday = time.mktime(date(2026, 8, 10).timetuple()) + 15.75 * 3600
    tuesday = time.mktime(date(2026, 8, 11).timetuple()) + 9.75 * 3600
    _save("H1", opened_at=monday)
    db_paper.cmd_save_close(
        _ns(
            data=json.dumps(
                {
                    "order_id": "H1",
                    "exit_debit": 1.0,
                    "pnl": 100.0,
                    "closed_at": tuesday,
                    "exit_reason": "profit_target",
                }
            )
        )
    )
    row = db_paper.cmd_get_pnl_summary(_ns(strategy=None, profile=None))["trades"][0]
    assert row["exit_reason"] == "profit_target" and row["hold_days"] == 1 and row["status"] == "closed"


# --------------------------------------------------------------------------- measurement breaks
def test_a_measurement_break_is_upserted_not_duplicated():
    """Re-running the change that caused a break must not double the record."""
    spec = {"break_date": "2026-08-12", "key": "lifecycle_cutover", "note": "first"}
    db_paper.cmd_record_measurement_break(_ns(data=json.dumps(spec)))
    db_paper.cmd_record_measurement_break(_ns(data=json.dumps({**spec, "note": "second"})))

    breaks = db_paper.cmd_get_measurement_breaks(_ns())["breaks"]
    assert len(breaks) == 1 and breaks[0]["note"] == "second"


def test_a_measurement_break_needs_a_date_and_a_key():
    assert db_paper.cmd_record_measurement_break(_ns(data=json.dumps({"key": "x"})))["ok"] is False


# --------------------------------------------------------------------------- migrating history
def _legacy_db(path):
    """A paper database shaped the way it was before any lifecycle column existed — the full
    pre-lifecycle `trades` column set, so the migration is exercised against the real starting
    point rather than a convenient subset of it."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE trades ("
        " order_id TEXT PRIMARY KEY, strategy TEXT NOT NULL DEFAULT 'iron_fly', symbol TEXT NOT NULL,"
        " expiration TEXT NOT NULL, short_strike REAL, long_call_strike REAL, long_put_strike REAL,"
        " legs_json TEXT, entry_credit REAL, exit_debit REAL, pnl REAL, opened_at REAL, closed_at REAL,"
        " profile TEXT NOT NULL DEFAULT 'default', quantity INTEGER, capital_at_risk REAL,"
        " entry_cost REAL, exit_cost REAL, entry_context TEXT, entry_iv REAL, exit_iv REAL,"
        " close_attempts INTEGER NOT NULL DEFAULT 0, last_close_error TEXT, last_close_attempt_at REAL,"
        " entry_slippage REAL, exit_slippage REAL)"
    )
    conn.executemany(
        "INSERT INTO trades (order_id, strategy, symbol, expiration, opened_at, closed_at, "
        "close_attempts) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("OLD_CLOSED", "iron_fly", "AAPL", "2026-01-16", 1.0, 2.0, 0),
            ("OLD_OPEN", "iron_condor", "MSFT", "2026-01-16", 1.0, None, 0),
            ("OLD_STUCK", "iron_fly", "HALT", "2026-01-16", 1.0, None, 3),
        ],
    )
    conn.commit()
    conn.close()


def test_migrating_a_legacy_database_does_not_relabel_its_history(tmp_path, monkeypatch):
    """Adding status NOT NULL DEFAULT 'open' would have marked every historical closed trade open,
    invisibly. The column arrives nullable and a backfill reads the facts already on the row."""
    path = tmp_path / "legacy.db"
    _legacy_db(path)
    monkeypatch.setattr(db_paper, "DB_PATH", path)
    db_paper.cmd_init_db(argparse.Namespace())

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = {r["order_id"]: dict(r) for r in conn.execute("SELECT * FROM trades").fetchall()}
    finally:
        conn.close()
    assert rows["OLD_CLOSED"]["status"] == "closed"
    assert rows["OLD_OPEN"]["status"] == "open"
    assert rows["OLD_STUCK"]["status"] == "stranded"


def test_a_legacy_exit_is_named_rather_than_left_blank(tmp_path, monkeypatch):
    """Every historical exit was the unconditional next-morning sweep. Saying so keeps a
    pre-lifecycle exit distinguishable from a managed exit whose reason failed to record."""
    path = tmp_path / "legacy.db"
    _legacy_db(path)
    monkeypatch.setattr(db_paper, "DB_PATH", path)
    db_paper.cmd_init_db(argparse.Namespace())

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = {r["order_id"]: dict(r) for r in conn.execute("SELECT * FROM trades").fetchall()}
    finally:
        conn.close()
    assert rows["OLD_CLOSED"]["exit_reason"] == "legacy_next_morning"
    assert rows["OLD_OPEN"]["exit_reason"] is None  # never closed, so nothing to name


def test_migration_is_idempotent(tmp_path, monkeypatch):
    """The backfill runs when its column is first added, never again — a second pass must not
    overwrite a status the lifecycle has since set."""
    path = tmp_path / "legacy.db"
    _legacy_db(path)
    monkeypatch.setattr(db_paper, "DB_PATH", path)
    db_paper.cmd_init_db(argparse.Namespace())

    conn = sqlite3.connect(path)
    conn.execute("UPDATE trades SET status = 'stranded' WHERE order_id = 'OLD_OPEN'")
    conn.commit()
    conn.close()

    db_paper.cmd_init_db(argparse.Namespace())  # migrate again
    conn = sqlite3.connect(path)
    try:
        status = conn.execute("SELECT status FROM trades WHERE order_id = 'OLD_OPEN'").fetchone()[0]
    finally:
        conn.close()
    assert status == "stranded"


def test_a_new_trade_in_a_migrated_database_still_gets_a_status(tmp_path, monkeypatch):
    """The migrated column is nullable, so an INSERT that left status to a default would produce
    NULL — every reader filtering on status would then miss the newest trades."""
    path = tmp_path / "legacy.db"
    _legacy_db(path)
    monkeypatch.setattr(db_paper, "DB_PATH", path)
    db_paper.cmd_init_db(argparse.Namespace())

    _save("NEW")
    rows = {r["order_id"]: r for r in db_paper.cmd_get_open_positions(_ns())["positions"]}
    assert rows["NEW"]["status"] == "open"


# --------------------------------------------------------------------------- the EOD lifecycle sections
def _eod(tmp_path, monkeypatch, day):
    from cherrypick.earnings import strat_test_harness as harness
    from cherrypick.earnings import strategy_metrics as metrics

    monkeypatch.setattr(metrics, "DB_PATH", db_paper.DB_PATH)
    monkeypatch.setattr(harness, "_eod_report_path", lambda d: tmp_path / f"paper-eod-{d}.md")
    return harness._write_eod_report(day).read_text(encoding="utf-8")


def test_the_eod_report_says_why_each_position_closed(tmp_path, monkeypatch):
    """Under a managed lifecycle the reason IS the finding: a session of profit targets and one of
    stops produce the same P&L line and mean entirely different things."""
    day = date.today().isoformat()
    _save("A", opened_at=time.time() - 86400)
    db_paper.cmd_save_close(
        _ns(data=json.dumps({"order_id": "A", "exit_debit": 1.0, "pnl": 100.0, "exit_reason": "pead_loser"}))
    )
    assert "pead_loser" in _eod(tmp_path, monkeypatch, day)


def test_the_eod_report_marks_positions_still_carrying_risk(tmp_path, monkeypatch):
    """Positions were force-closed the next morning before this change, so the section had nothing
    to say. A carried winner's mid-flight worth is what says whether carrying it was right."""
    day = date.today().isoformat()
    _save("B", capital_at_risk=500.0, opened_at=time.time() - 86400)
    _mark("B", usable=True, exit_debit=2.4, unrealized_pnl=60.0, session_date=day)

    report = _eod(tmp_path, monkeypatch, day)
    assert "Still open (carrying risk now)" in report and "$60.00" in report


def test_a_refused_mark_is_never_reported_as_a_valuation(tmp_path, monkeypatch):
    """It records that we looked and could not price it. Printing one as the position's worth would
    put a number in the report that no quote ever supported."""
    day = date.today().isoformat()
    _save("C", capital_at_risk=500.0, opened_at=time.time() - 86400)
    _mark("C", usable=False, refusal="missing_leg_quotes", unrealized_pnl=-9999.0, session_date=day)

    report = _eod(tmp_path, monkeypatch, day)
    assert "_unpriced_" in report and "-9999" not in report


def test_the_eod_report_separates_a_thin_feed_from_a_quiet_day(tmp_path, monkeypatch):
    day = date.today().isoformat()
    _save("D", opened_at=time.time() - 86400)
    _mark("D", usable=True, exit_debit=1.0, unrealized_pnl=10.0, session_date=day)
    _mark("D", usable=False, refusal="missing_leg_quotes", session_date=day)

    report = _eod(tmp_path, monkeypatch, day)
    assert "Feed quality" in report and "missing_leg_quotes x1" in report
