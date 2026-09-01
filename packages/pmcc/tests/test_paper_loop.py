"""End-to-end sessions against a fixture cache: entry, hold-to-expiry, settlement, disposal."""

from datetime import datetime

import pytest

from cherrypick.pmcc import analytics, db, paper_loop
from cherrypick.pmcc import book as bookmod


def _fill_entry_chains(cache):
    """The 2026-08-24 (Monday) plan: short Fri 2026-09-04 (11 DTE), long Fri 2026-09-11 (18 DTE).

    Short candidate at 71 (0.40 away, OTM) vs. none closer -- the ATM rule takes it regardless of
    moneyness. Long candidate at 58 carries delta 0.88 (inside [0.85, 0.90]) and sits inside the
    default 20% deep window (spot 70.60 x 0.8 = 56.48 floor)."""
    cache.spot("TQQQ", 70.60)
    cache.option("TQQQ", "2026-09-04", 71.0, bid=0.90, ask=1.00)
    cache.option("TQQQ", "2026-09-11", 58.0, bid=14.40, ask=14.60, delta=0.88)


def test_entry_day_control_only(cache, config, tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    _fill_entry_chains(cache)
    when = datetime(2026, 8, 24, 11, 0)
    result = paper_loop.run_once(config, conn, cache_path=cache.path, when=when)
    assert result["ok"], result

    positions = db.open_positions(conn)
    assert {p["book"] for p in positions} == {"control"}
    p = positions[0]
    assert p["long_strike"] == 58.0
    assert p["short_strike"] == 71.0
    assert p["net_debit"] == pytest.approx(14.50 - 0.95)
    assert p["short_expiration"] == "2026-09-04"
    assert p["era"] == analytics.CURRENT_ERA

    # Idempotence: a tick retry cannot double-enter.
    paper_loop.run_once(config, conn, cache_path=cache.path, when=when)
    assert len(db.open_positions(conn)) == 1


def test_holds_to_expiration_then_closes(cache, config, tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    _fill_entry_chains(cache)
    when = datetime(2026, 8, 24, 11, 0)
    paper_loop.run_once(config, conn, cache_path=cache.path, when=when)

    # Mid-week, well before the short's own expiration: the default control book holds regardless
    # of how the short's time value has moved (the 2026-08-23 redesign dropped the tv trigger).
    cache.option("TQQQ", "2026-09-04", 71.0, bid=0.05, ask=0.10)
    midweek = datetime(2026, 8, 26, 13, 0)
    paper_loop.run_once(config, conn, cache_path=cache.path, when=midweek)
    assert len(db.open_positions(conn)) == 1  # holds are not recorded as events -- only acted verdicts are

    # On the short's own expiration day, control closes both legs actively.
    cache.spot("TQQQ", 70.90)
    cache.option("TQQQ", "2026-09-04", 71.0, bid=0.20, ask=0.25)
    cache.option("TQQQ", "2026-09-11", 58.0, bid=14.65, ask=14.85)
    exp_day = datetime(2026, 9, 4, 11, 0)
    paper_loop.run_once(config, conn, cache_path=cache.path, when=exp_day)
    assert db.open_positions(conn) == []
    closed = conn.execute("SELECT * FROM pmcc_positions WHERE status = 'closed'").fetchone()
    assert closed["exit_reason"] == "short_expiration"
    legs = db.legs_for(conn, closed["position_id"])
    assert all(leg["status"] == "closed" and leg["close_kind"] == "traded" for leg in legs)


def _seed_position(conn, *, short_exp="2026-08-28", long_exp="2026-09-04"):
    pid = "TQQQ:control:2026-08-24"
    db.save_position(
        conn,
        {
            "position_id": pid,
            "symbol": "TQQQ",
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
            "occ_symbol": "TQQQ  260904C00050000",
            "streamer_symbol": ".TQQQ260904C50",
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
            "occ_symbol": "TQQQ  260828C00067000",
            "streamer_symbol": ".TQQQ260828C67",
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
    cache.spot("TQQQ", 68.10)
    cache.option("TQQQ", "2026-09-04", 50.0, bid=18.20, ask=18.40)
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

    cache.spot("TQQQ", 66.90)
    cache.option("TQQQ", "2026-09-04", 50.0, bid=16.90, ask=17.10)
    paper_loop.run_once(config, conn, cache_path=cache.path, when=datetime(2026, 8, 31, 10, 0))
    position = conn.execute("SELECT * FROM pmcc_positions WHERE position_id = ?", (pid,)).fetchone()
    assert position["status"] == "closed"
    expected = (4.75 - 0.0) * 100 + (17.00 - 20.70) * 100
    assert position["gross_pnl"] == pytest.approx(expected, abs=0.01)


def test_missed_settlement_is_never_backfilled(cache, config, tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    _seed_position(conn)
    cache.spot("TQQQ", 68.0)
    # Monday, with Friday's expiration still open: the loop flags it, does not settle it.
    result = paper_loop.run_once(config, conn, cache_path=cache.path, when=datetime(2026, 8, 31, 10, 0))
    assert result["ok"]
    short_leg = conn.execute("SELECT status FROM pmcc_legs WHERE leg_role = 'short_call_1'").fetchone()
    assert short_leg["status"] == "open"


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
        "stream_cache",
        "stream_cache_present",
        "data_ok",
        "data_reason",
    ):
        assert key in status
    assert status["session_settled"] is True


def test_ex_dividend_span_refused(cache, config, tmp_path):
    config["dividends"]["TQQQ"] = {"declared_through": "2099-12-31", "ex_dates": ["2026-09-01"]}
    conn = db.connect(str(tmp_path / "paper.db"))
    _fill_entry_chains(cache)
    paper_loop.run_once(config, conn, cache_path=cache.path, when=datetime(2026, 8, 24, 11, 0))
    assert db.open_positions(conn) == []
    outcomes = {
        r["outcome"] for r in conn.execute("SELECT outcome FROM pmcc_entry_attempts WHERE symbol = 'TQQQ'")
    }
    assert outcomes == {"ex_dividend_span"}


def test_lapsed_dividend_calendar_refuses(cache, config, tmp_path):
    config["dividends"]["TQQQ"] = {"declared_through": "2026-08-20", "ex_dates": []}
    conn = db.connect(str(tmp_path / "paper.db"))
    _fill_entry_chains(cache)
    paper_loop.run_once(config, conn, cache_path=cache.path, when=datetime(2026, 8, 24, 11, 0))
    assert db.open_positions(conn) == []
    outcomes = {r["outcome"] for r in conn.execute("SELECT outcome FROM pmcc_entry_attempts")}
    assert outcomes == {"dividend_calendar_lapsed"}


def test_entry_guards_skip_ex_div_for_cash_settled_symbol(config):
    # XSP (cash-settled) must never be refused on ex-dividend grounds, even with a lapsed/undeclared
    # dividend calendar or a span containing a declared ex-date -- the whole check is gated on
    # settlement_style == "physical" in _entry_guards, and a cash symbol needs no dividends entry.
    config["settlement_style"]["XSP"] = "cash"
    plan_dates = {"short_expiration": "2026-09-01"}
    assert paper_loop._entry_guards(config, "XSP", plan_dates, "2026-08-24") is None
    # The TQQQ (physical) case is unaffected: still refused without dividend coverage.
    del config["dividends"]["TQQQ"]
    assert paper_loop._entry_guards(config, "TQQQ", plan_dates, "2026-08-24") == "dividend_calendar_lapsed"


def test_entry_guards_refuses_unknown_settlement():
    assert paper_loop._entry_guards({}, "XSP", {"short_expiration": "2026-09-01"}, "2026-08-24") == (
        "unknown_settlement"
    )


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
