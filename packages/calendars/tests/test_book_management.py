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


def test_a_penny_wide_leg_is_not_too_wide_to_close():
    """The 2026-08-28 defect, in one test.

    The control put's front leg quoted `bid 0.00 / ask 0.01` into its exit window. As a ratio that
    is exactly 2.000 -- a "200% spread" -- and it refused the scheduled Friday close on all thirty
    ticks of the window, while the call side closed normally at 0.222. The position missed its exit,
    its front expired instead, the longs went on Monday, and the result differed from the policy
    replay by $1.30, which is how the disagreement surfaced at all.

    A short that has gone almost worthless is the WIN case. Refusing to close it because a
    one-cent quote reads as a wide percentage is the gate working against the thing it protects.
    """
    params = management.effective_params(_pos(), {})
    now = _at("2026-08-21", "10:00")
    cheap = {
        "ok": True,
        "max_spread_pct": 2.0,
        "leg_spreads": [
            {"symbol": ".SPY260828P600", "pct": 2.0, "abs": 0.01},
            {"symbol": ".SPY260918P600", "pct": 0.154, "abs": 0.01},
        ],
    }
    assert management.execution_gate(cheap, params, now=now) is None


def test_a_leg_wide_in_money_as_well_as_percent_still_blocks():
    """The gate must still do its job. Only the cheap case is exempted, and cheapness is measured
    in money -- a leg that is wide on both readings is genuinely illiquid and refusing it is right."""
    params = management.effective_params(_pos(), {})
    now = _at("2026-08-21", "10:00")
    wide = {
        "ok": True,
        "max_spread_pct": 2.0,
        "leg_spreads": [{"symbol": ".SPY260828P600", "pct": 2.0, "abs": 0.60}],
    }
    assert management.execution_gate(wide, params, now=now) == "spread_too_wide"


def test_the_two_readings_are_judged_per_leg_not_as_separate_maxima():
    """The widest-by-percent and the widest-by-money can be different legs. Comparing two separate
    maxima would refuse a structure that no single leg justifies -- here the cheap leg supplies the
    percentage and the expensive one supplies the money, and neither is actually unclosable."""
    params = management.effective_params(_pos(), {})
    now = _at("2026-08-21", "10:00")
    snapshot = {
        "ok": True,
        "max_spread_pct": 2.0,
        "leg_spreads": [
            {"symbol": "cheap-and-wide-in-pct", "pct": 2.0, "abs": 0.01},
            {"symbol": "wide-in-money-but-tight", "pct": 0.05, "abs": 0.60},
        ],
    }
    assert management.execution_gate(snapshot, params, now=now) is None


def test_a_snapshot_without_leg_detail_keeps_the_old_percentage_test():
    """A mark recorded before this change carries no per-leg detail. It must not silently admit
    more than it used to -- absent evidence is not evidence of a penny-wide leg."""
    params = management.effective_params(_pos(), {})
    now = _at("2026-08-21", "10:00")
    assert management.execution_gate({"ok": True, "max_spread_pct": 2.0}, params, now=now) == "spread_too_wide"


# --------------------------------------------------------------- physical settlement (SPY shape)
SPY_CONFIG = {"settlement_style": {"SPY": "physical"}}


def _spy_plan():
    """The same structure priced as SPY: strikes near 780, so a 770 settlement puts the PUT side
    ITM and leaves the call side worthless — one assignment, one expiry, per week."""
    plan = _plan()
    plan["symbol"] = "SPY"
    plan["spot"] = 780.0
    for side, strike in (("put", 776.0), ("call", 784.0)):
        plan["sides"][side]["strike"] = strike
        for leg in plan["sides"][side]["legs"]:
            leg["strike"] = strike
    return plan


def test_physical_settlement_delivers_shares_and_holds_the_week_open(conn):
    book.enter_week(conn, _spy_plan(), SPY_CONFIG, ["path"], week=WEEK, advice_params=None)
    book.settle_expiring_legs(conn, "2026-08-21", 770.0, SPY_CONFIG, symbol="SPY")

    rows = {r["leg_role"]: r for r in conn.execute("SELECT * FROM dc_assignments")}
    assert set(rows) == {"front_put"}, "only the ITM short delivers; the OTM call expires worthless"
    assigned = rows["front_put"]
    assert assigned["direction"] == "long"  # a short put assigned means you bought the shares
    assert assigned["shares"] == 100
    assert assigned["basis"] == 770.0  # the settlement spot, not the 776 strike
    assert assigned["status"] == "open"

    # The option leg still books at intrinsic under either style; only its kind records the delivery.
    front_put = conn.execute(
        "SELECT * FROM dc_legs WHERE leg_role = 'front_put' AND position_id LIKE '%:put'"
    ).fetchone()
    assert front_put["close_kind"] == "assigned"
    assert front_put["close_value"] == 6.0  # 776 - 770

    # No $5 charged yet: a physical assignment pays at disposal, when the price is known.
    put_pos = conn.execute("SELECT * FROM dc_positions WHERE side = 'put'").fetchone()
    assert (put_pos["exit_cost"] or 0.0) == 0.0


def test_a_week_cannot_close_while_its_shares_are_still_held(conn):
    book.enter_week(conn, _spy_plan(), SPY_CONFIG, ["control"], week=WEEK, advice_params=None)
    book.settle_expiring_legs(conn, "2026-08-21", 770.0, SPY_CONFIG, symbol="SPY")
    # Close the surviving long so every OPTION leg is done — the shares alone must hold it open.
    pid = "2026-08-17:control:put"
    db.save_leg(conn, {"position_id": pid, "leg_role": "back_put", "status": "closed",
                       "close_kind": "traded", "close_value": 8.0})
    assert book.finalize_if_done(conn, pid, reason="test", session_date="2026-08-24") is False
    assert conn.execute(
        "SELECT status FROM dc_positions WHERE position_id = ?", (pid,)
    ).fetchone()["status"] != "closed"


def test_disposal_books_the_weekend_move_and_then_the_week_closes(conn):
    book.enter_week(conn, _spy_plan(), SPY_CONFIG, ["control"], week=WEEK, advice_params=None)
    book.settle_expiring_legs(conn, "2026-08-21", 770.0, SPY_CONFIG, symbol="SPY")
    pid = "2026-08-17:control:put"
    db.save_leg(conn, {"position_id": pid, "leg_role": "back_put", "status": "closed",
                       "close_kind": "traded", "close_value": 8.0})

    assignment = db.open_assignments(conn, before_session="2026-08-24")[0]
    result = book.dispose_assignment(conn, assignment, 774.5, session_date="2026-08-24")

    # Long 100 shares based at 770 sold at 774.50.
    assert result["share_pnl"] == 450.0
    row = conn.execute("SELECT * FROM dc_assignments").fetchone()
    assert row["status"] == "disposed" and row["disposal_price"] == 774.5
    assert row["disposed_session"] == "2026-08-24" and row["assigned_session"] == "2026-08-21"

    position = conn.execute("SELECT * FROM dc_positions WHERE position_id = ?", (pid,)).fetchone()
    assert position["status"] == "closed"
    # Options: short put 20 -> 6 (+14), long put 25 -> 8 (-17) = -3.00/share = -300, plus +450.
    assert position["gross_pnl"] == 150.0


def test_shares_delivered_tonight_are_not_disposable_tonight(conn):
    """The weekend exposure is the point, not an artefact — settlement hands the shares over after
    the close, so the earliest disposal is the next session."""
    book.enter_week(conn, _spy_plan(), SPY_CONFIG, ["path"], week=WEEK, advice_params=None)
    book.settle_expiring_legs(conn, "2026-08-21", 770.0, SPY_CONFIG, symbol="SPY")
    assert db.open_assignments(conn, before_session="2026-08-21") == []
    assert len(db.open_assignments(conn, before_session="2026-08-24")) == 1


def test_a_cash_settled_week_never_grows_a_share_row(conn):
    book.enter_week(conn, _plan(), {}, ["path"], week=WEEK, advice_params=None)
    book.settle_expiring_legs(conn, "2026-08-21", 6440.0, {}, symbol="SPX")
    assert conn.execute("SELECT COUNT(*) FROM dc_assignments").fetchone()[0] == 0
    put_pos = conn.execute("SELECT * FROM dc_positions WHERE side = 'put'").fetchone()
    assert put_pos["exit_cost"] == 5.00  # the ITM cash settlement still pays at settlement
