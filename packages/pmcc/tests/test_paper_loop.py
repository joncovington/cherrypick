"""End-to-end sessions against a fixture cache: entry, the tv close, settlement, disposal."""

from datetime import datetime

import pytest

from cherrypick.pmcc import book as bookmod
from cherrypick.pmcc import db, paper_loop


def _fill_entry_chains(cache):
    """The 2026-08-24 (Monday) plan: short Fri 2026-09-04, long Fri 2026-09-11."""
    cache.spot("TNA", 70.60)
    cache.option("TNA", "2026-09-04", 67.0, bid=4.70, ask=4.80, delta=0.72)
    cache.option("TNA", "2026-09-11", 50.0, bid=20.60, ask=20.80, delta=0.99)


def test_entry_day_control_and_roll_pair_keltner_refuses(cache, config, tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    _fill_entry_chains(cache)
    when = datetime(2026, 8, 24, 11, 0)
    result = paper_loop.run_once(config, conn, cache_path=cache.path, when=when)
    assert result["ok"], result

    positions = db.open_positions(conn)
    assert {p["book"] for p in positions} == {"control", "roll"}
    # Exact pairing: identical fills from one plan.
    a, b = positions
    assert (a["long_strike"], a["short_strike"], a["net_debit"]) == (
        b["long_strike"],
        b["short_strike"],
        b["net_debit"],
    )
    assert a["short_strike"] == 67.0
    assert a["long_strike"] == 50.0
    assert a["net_debit"] == pytest.approx(20.70 - 4.75)
    assert a["entry_downside_protection_pct"] == pytest.approx((70.60 - 67.0) / 70.60, abs=1e-6)

    keltner_attempt = conn.execute(
        "SELECT outcome FROM pmcc_entry_attempts WHERE book = 'keltner'"
    ).fetchone()
    assert keltner_attempt["outcome"] == "insufficient_bar_history"

    # Idempotence: a tick retry cannot double-enter.
    paper_loop.run_once(config, conn, cache_path=cache.path, when=when)
    assert len(db.open_positions(conn)) == 2


def test_tv_exhaustion_closes_both_legs_and_flags_exposure(cache, config, tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    _fill_entry_chains(cache)
    when = datetime(2026, 8, 24, 11, 0)
    paper_loop.run_once(config, conn, cache_path=cache.path, when=when)

    # The short decays to 0.03 of time value with spot unmoved: exposed tick, then both legs close.
    cache.option("TNA", "2026-09-04", 67.0, bid=3.62, ask=3.64, delta=0.95)
    later = datetime(2026, 8, 24, 13, 0)
    paper_loop.run_once(config, conn, cache_path=cache.path, when=later)

    assert db.open_positions(conn) == []
    closed = [dict(r) for r in conn.execute("SELECT * FROM pmcc_positions WHERE status = 'closed'")]
    assert len(closed) == 2
    for p in closed:
        assert p["exit_reason"] == "tv_exhausted"
        # gross: short earns 4.75 - 3.63, long flat at 20.70.
        assert p["gross_pnl"] == pytest.approx((4.75 - 3.63) * 100, abs=0.01)
        assert p["fees"] > 0
        legs = db.legs_for(conn, p["position_id"])
        assert all(leg["status"] == "closed" and leg["close_kind"] == "traded" for leg in legs)
    exposed = conn.execute("SELECT COUNT(*) FROM pmcc_marks WHERE assignment_exposed = 1").fetchone()[0]
    assert exposed >= 2  # one short-leg mark per book on the closing tick


def _seed_position(conn, *, short_exp="2026-08-28", long_exp="2026-09-04"):
    pid = "TNA:control:2026-08-24"
    db.save_position(
        conn,
        {
            "position_id": pid,
            "symbol": "TNA",
            "book": "control",
            "entry_session": "2026-08-24",
            "quantity": 1,
            "long_expiration": long_exp,
            "long_strike": 50.0,
            "short_expiration": short_exp,
            "short_strike": 67.0,
            "net_debit": 15.95,
            "status": "open",
            "fees": 0.0,
        },
    )
    db.save_leg(
        conn,
        {
            "position_id": pid,
            "leg_role": "long_call",
            "occ_symbol": "TNA   260904C00050000",
            "streamer_symbol": ".TNA260904C50",
            "expiration": long_exp,
            "strike": 50.0,
            "option_type": "call",
            "action": "Buy to Open",
            "quantity": 1,
            "entry_mid": 20.70,
            "status": "open",
        },
    )
    db.save_leg(
        conn,
        {
            "position_id": pid,
            "leg_role": "short_call_1",
            "occ_symbol": "TNA   260828C00067000",
            "streamer_symbol": ".TNA260828C67",
            "expiration": short_exp,
            "strike": 67.0,
            "option_type": "call",
            "action": "Sell to Open",
            "quantity": 1,
            "entry_mid": 4.75,
            "status": "open",
        },
    )
    return pid


def test_itm_settlement_delivers_short_shares_then_combined_disposal(cache, config, tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    pid = _seed_position(conn)

    # Friday 2026-08-28, official print 69.20: the short is ITM and assigns.
    settle = paper_loop.run_settle(
        config,
        conn,
        cache_path=cache.path,
        when=datetime(2026, 8, 28, 16, 30),
        price=69.20,
        day="2026-08-28",
    )
    assert settle["ok"], settle
    position = conn.execute("SELECT * FROM pmcc_positions WHERE position_id = ?", (pid,)).fetchone()
    assert position["status"] == "short_settled"
    assignment = db.assignments_for(conn, pid)[0]
    assert assignment["direction"] == "short"
    assert assignment["shares"] == 100
    assert assignment["basis"] == 69.20
    short_leg = [leg for leg in db.legs_for(conn, pid) if leg["leg_role"] == "short_call_1"][0]
    assert short_leg["close_kind"] == "assigned"
    assert short_leg["close_value"] == pytest.approx(2.20)

    # Monday 2026-08-31: cover the shares at spot and sell the long at its mark, together.
    cache.spot("TNA", 68.10)
    cache.option("TNA", "2026-09-04", 50.0, bid=18.20, ask=18.40)
    result = paper_loop.run_once(config, conn, cache_path=cache.path, when=datetime(2026, 8, 31, 10, 0))
    assert result["ok"], result
    position = conn.execute("SELECT * FROM pmcc_positions WHERE position_id = ?", (pid,)).fetchone()
    assert position["status"] == "closed"
    # gross: short (4.75 - 2.20) + long (18.30 - 20.70), per share x100, plus the share leg
    # (69.20 - 68.10) x 100 short shares.
    expected = (4.75 - 2.20) * 100 + (18.30 - 20.70) * 100 + (69.20 - 68.10) * 100
    assert position["gross_pnl"] == pytest.approx(expected, abs=0.01)
    assert position["fees"] > 0  # the $5 assignment event + pass-throughs + close costs
    assignment = db.assignments_for(conn, pid)[0]
    assert assignment["status"] == "disposed"
    assert assignment["disposal_price"] == 68.10


def test_otm_expiry_orphan_long_disposed_next_session(cache, config, tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    pid = _seed_position(conn)
    settle = paper_loop.run_settle(
        config,
        conn,
        cache_path=cache.path,
        when=datetime(2026, 8, 28, 16, 30),
        price=66.40,
        day="2026-08-28",
    )
    assert settle["ok"]
    short_leg = [leg for leg in db.legs_for(conn, pid) if leg["leg_role"] == "short_call_1"][0]
    assert short_leg["close_kind"] == "expired"
    assert short_leg["close_value"] == 0.0
    assert db.assignments_for(conn, pid)[0:0] == []  # nothing delivered

    cache.spot("TNA", 66.90)
    cache.option("TNA", "2026-09-04", 50.0, bid=16.90, ask=17.10)
    paper_loop.run_once(config, conn, cache_path=cache.path, when=datetime(2026, 8, 31, 10, 0))
    position = conn.execute("SELECT * FROM pmcc_positions WHERE position_id = ?", (pid,)).fetchone()
    assert position["status"] == "closed"
    expected = (4.75 - 0.0) * 100 + (17.00 - 20.70) * 100
    assert position["gross_pnl"] == pytest.approx(expected, abs=0.01)


def test_missed_settlement_is_never_backfilled(cache, config, tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    _seed_position(conn)
    cache.spot("TNA", 68.0)
    # Monday, with Friday's expiration still open: the loop flags it, does not settle it.
    result = paper_loop.run_once(config, conn, cache_path=cache.path, when=datetime(2026, 8, 31, 10, 0))
    assert result["ok"]
    short_leg = conn.execute("SELECT status FROM pmcc_legs WHERE leg_role = 'short_call_1'").fetchone()
    assert short_leg["status"] == "open"


def test_roll_book_rolls_on_breach(cache, config, tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    pid = _seed_position(conn)
    conn.execute("UPDATE pmcc_positions SET book = 'roll' WHERE position_id = ?", (pid,))
    conn.commit()

    # Spot breaches to 64. Mark quotes for both legs; a roll chain on the plan's landing Friday
    # (roll_expiration from 2026-08-24 capped by the 09-04 long -> 09-04 itself, nearest 9d target).
    cache.spot("TNA", 64.0)
    cache.option("TNA", "2026-09-04", 50.0, bid=14.20, ask=14.40)
    cache.option("TNA", "2026-08-28", 67.0, bid=0.55, ask=0.65)
    cache.option("TNA", "2026-09-04", 60.0, bid=4.55, ask=4.65)  # tv 0.60 at spot 64 — rolls here

    result = paper_loop.run_once(config, conn, cache_path=cache.path, when=datetime(2026, 8, 24, 12, 0))
    assert result["ok"]
    position = conn.execute("SELECT * FROM pmcc_positions WHERE position_id = ?", (pid,)).fetchone()
    assert position["roll_count"] == 1
    assert position["short_strike"] == 60.0
    assert position["short_expiration"] == "2026-09-04"
    legs = {leg["leg_role"]: leg for leg in db.legs_for(conn, pid)}
    assert legs["short_call_1"]["close_kind"] == "rolled"
    assert legs["short_call_2"]["status"] == "open"
    assert legs["short_call_2"]["strike"] == 60.0
    event = conn.execute("SELECT executed FROM pmcc_management_events WHERE action = 'roll_short'").fetchone()
    assert event["executed"] == 1

    # Same session: the roll cadence holds a second breach tick.
    result = paper_loop.run_once(config, conn, cache_path=cache.path, when=datetime(2026, 8, 24, 12, 30))
    position = conn.execute("SELECT * FROM pmcc_positions WHERE position_id = ?", (pid,)).fetchone()
    assert position["roll_count"] == 1


def test_run_status_contract(cache, config, tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    status = paper_loop.run_status(config, conn, cache_path=cache.path)
    for key in (
        "ok",
        "date",
        "in_session",
        "session_settled",
        "positions_today",
        "open_positions",
        "expiration_plan",
        "keltner_days",
        "stream_cache",
        "stream_cache_present",
        "data_ok",
        "data_reason",
    ):
        assert key in status
    assert status["session_settled"] is True
    assert status["keltner_days"] == {"TNA": 0}


def test_ex_dividend_span_refused(cache, config, tmp_path):
    config["dividends"]["TNA"] = {"declared_through": "2099-12-31", "ex_dates": ["2026-09-01"]}
    conn = db.connect(str(tmp_path / "paper.db"))
    _fill_entry_chains(cache)
    paper_loop.run_once(config, conn, cache_path=cache.path, when=datetime(2026, 8, 24, 11, 0))
    assert db.open_positions(conn) == []
    outcomes = {
        r["outcome"] for r in conn.execute("SELECT outcome FROM pmcc_entry_attempts WHERE symbol = 'TNA'")
    }
    assert outcomes == {"ex_dividend_span"}


def test_lapsed_dividend_calendar_refuses(cache, config, tmp_path):
    config["dividends"]["TNA"] = {"declared_through": "2026-08-20", "ex_dates": []}
    conn = db.connect(str(tmp_path / "paper.db"))
    _fill_entry_chains(cache)
    paper_loop.run_once(config, conn, cache_path=cache.path, when=datetime(2026, 8, 24, 11, 0))
    assert db.open_positions(conn) == []
    outcomes = {r["outcome"] for r in conn.execute("SELECT outcome FROM pmcc_entry_attempts")}
    assert outcomes == {"dividend_calendar_lapsed"}


def test_loop_lock_exclusive(tmp_path, monkeypatch):
    monkeypatch.setenv("PMCC_DB_PATH", str(tmp_path / "data" / "paper_trades.db"))
    assert paper_loop._acquire_loop_lock()
    try:
        assert not paper_loop._acquire_loop_lock()
    finally:
        paper_loop._release_loop_lock()
    assert paper_loop._acquire_loop_lock()
    paper_loop._release_loop_lock()


def test_finalize_refuses_while_shares_open(cache, config, tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    pid = _seed_position(conn)
    paper_loop.run_settle(
        config,
        conn,
        cache_path=cache.path,
        when=datetime(2026, 8, 28, 16, 30),
        price=69.20,
        day="2026-08-28",
    )
    # Close the long by hand; the open share position must still hold the close open.
    db.save_leg(
        conn,
        {
            "position_id": pid,
            "leg_role": "long_call",
            "status": "closed",
            "close_kind": "traded",
            "close_value": 19.0,
        },
    )
    assert not bookmod.finalize_if_done(conn, pid, reason="test", session_date="2026-08-31")
