"""Entries across books, traded closes, cash settlement, and the management verdicts."""

import json
from datetime import datetime

import pytest

from cherrypick.calendars import book, clock, db, management

WEEK = {
    "week_of": "2026-08-17",
    "entry_session": "2026-08-17",
    "front_expiration": "2026-08-21",
    "back_expiration": "2026-08-24",
    "structure": "dc_4_7",
}


def _plan():
    def leg(which, side, strike):
        return {
            "leg_role": f"{which}_{side}",
            "occ_symbol": f"SPXW  {which}{side}{strike:g}",
            "streamer_symbol": f".{which}_{side}",
            "expiration": WEEK["front_expiration"] if which == "front" else WEEK["back_expiration"],
            "strike": strike,
            "option_type": side,
            "action": "Sell to Open" if which == "front" else "Buy to Open",
            "bid": 19.8 if which == "front" else 24.7,
            "ask": 20.2 if which == "front" else 25.3,
            "mid": 20.0 if which == "front" else 25.0,
            "iv": 0.18,
            "delta": 0.3,
        }

    return {
        "symbol": "SPX",
        "spot": 6500.0,
        "em": 34.0,
        "em_pct": 0.00523,
        "front_atm_call_mid": 20.0,
        "front_atm_put_mid": 20.0,
        "front_iv": 0.2,
        "back_iv": 0.18,
        "term_structure": -0.11,
        "sides": {
            "put": {
                "strike": 6465.0,
                "target": 6466.0,
                "debit": 5.0,
                "legs": [leg("front", "put", 6465.0), leg("back", "put", 6465.0)],
            },
            "call": {
                "strike": 6535.0,
                "target": 6534.0,
                "debit": 5.0,
                "legs": [leg("front", "call", 6535.0), leg("back", "call", 6535.0)],
            },
        },
    }


def _mark_snapshot(front_mid=15.0, back_mid=22.0, spot=6500.0):
    def q(mid):
        return {"bid": mid - 0.2, "ask": mid + 0.2, "mid": mid, "age_seconds": 1.0}

    return {
        "ok": True,
        "spot": spot,
        "quotes": {
            ".front_put": q(front_mid),
            ".back_put": q(back_mid),
            ".front_call": q(front_mid),
            ".back_call": q(back_mid),
        },
        "greeks": {},
        "max_spread_pct": 0.03,
    }


@pytest.fixture()
def conn(tmp_path):
    return db.connect(str(tmp_path / "paper.db"))


def test_enter_week_writes_every_book_with_shared_fills(conn):
    opened = book.enter_week(
        conn,
        _plan(),
        {},
        ["control", "path", "advised:control"],
        week=WEEK,
        advice_params={"profit_target_pct": 0.2},
    )
    assert len(opened) == 6  # 3 books x 2 sides
    rows = conn.execute(
        "SELECT book, side, entry_debit, advice_params FROM dc_positions ORDER BY book, side"
    ).fetchall()
    assert {r["entry_debit"] for r in rows} == {5.0}  # identical fills across books
    frozen = {r["book"]: r["advice_params"] for r in rows}
    assert frozen["control"] is None and frozen["path"] is None
    assert json.loads(frozen["advised:control"]) == {"profit_target_pct": 0.2}
    assert conn.execute("SELECT COUNT(*) FROM dc_legs").fetchone()[0] == 12


def test_enter_week_is_idempotent(conn):
    book.enter_week(conn, _plan(), {}, ["control"], week=WEEK, advice_params=None)
    again = book.enter_week(conn, _plan(), {}, ["control"], week=WEEK, advice_params=None)
    assert again == []
    assert conn.execute("SELECT COUNT(*) FROM dc_positions").fetchone()[0] == 2


def test_close_open_legs_finalizes_with_gross_and_fees(conn):
    book.enter_week(conn, _plan(), {}, ["control"], week=WEEK, advice_params=None)
    position = dict(conn.execute("SELECT * FROM dc_positions WHERE side = 'put'").fetchone())
    result = book.close_open_legs(
        conn, position, _mark_snapshot(), {}, reason="scheduled_exit", session_date="2026-08-21"
    )
    assert result["ok"]
    row = conn.execute("SELECT * FROM dc_positions WHERE side = 'put'").fetchone()
    assert row["status"] == "closed"
    assert row["exit_reason"] == "scheduled_exit"
    assert row["closed_session"] == "2026-08-21"
    # Entered 5.00 debit (front 20 / back 25); closed at front 15 / back 22 -> value 7.00.
    # Gross = (20-15 on the short) + (22-25 on the long) = +5 -3 = +2.00/share = $200.
    assert row["gross_pnl"] == pytest.approx(200.0)
    assert row["fees"] > 0
    legs = conn.execute(
        "SELECT status, close_kind FROM dc_legs WHERE position_id = ?", (row["position_id"],)
    ).fetchall()
    assert all(leg["status"] == "closed" and leg["close_kind"] == "traded" for leg in legs)


def test_settlement_leaves_short_settled_then_disposition_closes(conn):
    book.enter_week(conn, _plan(), {}, ["path"], week=WEEK, advice_params=None)
    # Friday: spot 6440 — the 6465 put is ITM (intrinsic 25), the 6535 call OTM.
    results = book.settle_expiring_legs(conn, "2026-08-21", 6440.0, {}, symbol="SPX")
    assert len(results) == 2
    put_row = conn.execute("SELECT * FROM dc_positions WHERE side = 'put'").fetchone()
    call_row = conn.execute("SELECT * FROM dc_positions WHERE side = 'call'").fetchone()
    assert put_row["status"] == "short_settled" and call_row["status"] == "short_settled"
    assert put_row["itm_settlements"] == 1 and call_row["itm_settlements"] == 0
    assert put_row["settlement_spot"] == 6440.0
    put_front = conn.execute(
        "SELECT * FROM dc_legs WHERE position_id = ? AND leg_role = 'front_put'",
        (put_row["position_id"],),
    ).fetchone()
    assert put_front["status"] == "settled"
    assert put_front["close_kind"] == "cash_settled"
    assert put_front["close_value"] == 25.0

    # Monday: dispose the longs at their marks.
    for position in (put_row, call_row):
        result = book.close_open_legs(
            conn,
            dict(position),
            _mark_snapshot(back_mid=26.0),
            {},
            reason="long_disposition",
            session_date="2026-08-24",
        )
        assert result["ok"]
    put_row = conn.execute("SELECT * FROM dc_positions WHERE side = 'put'").fetchone()
    assert put_row["status"] == "closed"
    # Put side: short 20 -> settled 25 (-5), long 25 -> sold 26 (+1): gross -4.00/share.
    assert put_row["gross_pnl"] == pytest.approx(-400.0)
    # Fees: entry + $5 ITM settlement + disposition close fee, all accumulated.
    assert put_row["fees"] > 5.0


def _pos(book_name="control", side="put", **overrides):
    row = {
        "position_id": f"2026-08-17:{book_name}:{side}",
        "book": book_name,
        "side": side,
        "strike": 6465.0,
        "front_expiration": "2026-08-21",
        "back_expiration": "2026-08-24",
        "entry_debit": 5.0,
        "status": "open",
        "advice_params": None,
    }
    row.update(overrides)
    return row


def _at(day: str, hhmm: str) -> datetime:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=clock.ET)


def test_control_closes_only_in_the_friday_exit_window():
    params = management.effective_params(_pos(), {})
    early = management.evaluate(
        _pos(), params, now=_at("2026-08-21", "15:00"), combined_value=6.0, combined_debit=10.0, spot=6500.0
    )
    assert early.action == "hold"
    in_window = management.evaluate(
        _pos(), params, now=_at("2026-08-21", "15:46"), combined_value=6.0, combined_debit=10.0, spot=6500.0
    )
    assert in_window.action == "close_all"
    assert in_window.reason == "scheduled_exit"
    thursday = management.evaluate(
        _pos(), params, now=_at("2026-08-20", "15:50"), combined_value=6.0, combined_debit=10.0, spot=6500.0
    )
    assert thursday.action == "hold"


def test_path_always_holds():
    position = _pos("path")
    params = management.effective_params(position, {})
    decision = management.evaluate(
        position, params, now=_at("2026-08-21", "15:50"), combined_value=1.0, combined_debit=10.0, spot=6000.0
    )
    assert decision.action == "hold"
    assert decision.reason == "path_holds"


def test_advised_profit_target_and_stop_read_the_combined_double():
    position = _pos(
        "advised:control",
        advice_params=json.dumps(
            {"profit_target_pct": 0.20, "stop_loss_pct_of_debit": 0.50, "long_disposition": "mon_open"}
        ),
    )
    params = management.effective_params(position, {})
    pt = management.evaluate(
        position,
        params,
        now=_at("2026-08-18", "11:00"),
        combined_value=12.1,
        combined_debit=10.0,
        spot=6500.0,
    )
    assert (pt.action, pt.reason) == ("close_all", "profit_target")
    sl = management.evaluate(
        position, params, now=_at("2026-08-18", "11:00"), combined_value=4.9, combined_debit=10.0, spot=6500.0
    )
    assert (sl.action, sl.reason) == ("close_all", "stop_loss")
    hold = management.evaluate(
        position,
        params,
        now=_at("2026-08-18", "11:00"),
        combined_value=10.5,
        combined_debit=10.0,
        spot=6500.0,
    )
    assert hold.action == "hold"
    # mon_open means no whole-structure scheduled exit on Friday.
    friday = management.evaluate(
        position,
        params,
        now=_at("2026-08-21", "15:50"),
        combined_value=10.5,
        combined_debit=10.0,
        spot=6500.0,
    )
    assert friday.action == "hold"


def test_advised_touch_fires_on_the_touched_side_only():
    stamp = json.dumps({"short_strike_touch_exit": True, "long_disposition": "mon_open"})
    put_side = _pos("advised:control", advice_params=stamp)
    call_side = _pos("advised:control", side="call", strike=6535.0, advice_params=stamp)
    params_put = management.effective_params(put_side, {})
    params_call = management.effective_params(call_side, {})
    now = _at("2026-08-19", "13:00")
    touched = management.evaluate(
        put_side, params_put, now=now, combined_value=10.0, combined_debit=10.0, spot=6460.0
    )
    assert (touched.action, touched.reason) == ("close_all", "short_strike_touch")
    untouched = management.evaluate(
        call_side, params_call, now=now, combined_value=10.0, combined_debit=10.0, spot=6460.0
    )
    assert untouched.action == "hold"


def test_advised_thu_close_schedules_thursday():
    position = _pos("advised:control", advice_params=json.dumps({"time_exit": "thu_close"}))
    params = management.effective_params(position, {})
    decision = management.evaluate(
        position, params, now=_at("2026-08-20", "15:50"), combined_value=None, combined_debit=None, spot=None
    )
    assert (decision.action, decision.reason) == ("close_all", "scheduled_exit")


def test_execution_gate():
    params = management.effective_params(_pos(), {})
    now = _at("2026-08-21", "10:00")
    assert management.execution_gate({"ok": False}, params, now=now) == "unusable_mark"
    assert (
        management.execution_gate(
            {"ok": True, "max_spread_pct": 0.03}, params, now=_at("2026-08-21", "09:35")
        )
        == "before_exec_window"
    )
    assert (
        management.execution_gate({"ok": True, "max_spread_pct": 0.60}, params, now=now) == "spread_too_wide"
    )
    assert management.execution_gate({"ok": True, "max_spread_pct": 0.03}, params, now=now) is None
