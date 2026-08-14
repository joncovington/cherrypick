"""The exit-policy derivation over one synthetic week, validated against the real books.

The week is built through the SAME book functions the loop uses (enter, close, settle, dispose),
with a controlled mark path: profit targets trigger on successive days, the stop never fires, the
put strike is touched Thursday, and the control book closes at Friday's bell on the same tick the
derivation replays — so `validate_against_control` must come back clean to the cent.
"""

from datetime import datetime

import pytest

from cherrypick.calendars import book, clock, db, exit_policies

WEEK = {
    "week_of": "2026-08-17",
    "entry_session": "2026-08-17",
    "front_expiration": "2026-08-21",
    "back_expiration": "2026-08-24",
    "structure": "dc_4_7",
}
SETTLE_SPOT = 6440.0  # 6465 put ITM (25), 6535 call OTM


def _plan():
    def leg(which, side, strike):
        return {
            "leg_role": f"{which}_{side}",
            "occ_symbol": f"SPXW  {which}{side}",
            "streamer_symbol": f".{which}_{side}",
            "expiration": WEEK["front_expiration"] if which == "front" else WEEK["back_expiration"],
            "strike": strike,
            "option_type": side,
            "action": "Sell to Open" if which == "front" else "Buy to Open",
            "bid": 19.8 if which == "front" else 24.7,
            "ask": 20.2 if which == "front" else 25.3,
            "mid": 20.0 if which == "front" else 25.0,
            "iv": None,
            "delta": None,
        }

    return {
        "symbol": "SPX",
        "spot": 6500.0,
        "em": 34.0,
        "em_pct": 0.00523,
        "front_atm_call_mid": 20.0,
        "front_atm_put_mid": 20.0,
        "front_iv": None,
        "back_iv": None,
        "term_structure": None,
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


def _ts(day: str, hhmm: str) -> float:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=clock.ET).timestamp()


def _q(mid):
    return {"bid": round(mid - 0.2, 4), "ask": round(mid + 0.2, 4), "mid": mid, "age_seconds": 1.0}


def _tick(conn, day, hhmm, spot, marks, books=("control", "path")):
    """Record one tick's marks for every given book's open legs (roles present in `marks`)."""
    ts = _ts(day, hhmm)
    for book_name in books:
        for side in ("put", "call"):
            pid = f"{WEEK['week_of']}:{book_name}:{side}"
            for role_prefix, mid in marks.items():
                role = f"{role_prefix}_{side}"
                quote = _q(mid)
                db.record_mark(
                    conn,
                    position_id=pid,
                    leg_role=role,
                    marked_at=ts,
                    session_date=day,
                    bid=quote["bid"],
                    ask=quote["ask"],
                    mid=mid,
                    spot=spot,
                    usable=1,
                )
    return ts


def _snapshot_from(marks, spot):
    quotes = {}
    for side in ("put", "call"):
        for role_prefix, mid in marks.items():
            quotes[f".{role_prefix}_{side}"] = _q(mid)
    return {"ok": True, "spot": spot, "quotes": quotes, "greeks": {}, "max_spread_pct": 0.02}


@pytest.fixture()
def week_conn(tmp_path):
    """One fully-lived week: entered Monday, marked all week, control closed Friday, path settled
    Friday and disposed Monday."""
    conn = db.connect(str(tmp_path / "paper.db"))
    book.enter_week(conn, _plan(), {}, ["control", "path"], week=WEEK, advice_params=None)

    # The week's path. Combined debit is 10.0; combined value = 2 x (back - front).
    _tick(conn, "2026-08-17", "10:05", 6500.0, {"front": 20.0, "back": 25.0})  # entry tick: +0%
    _tick(conn, "2026-08-18", "11:00", 6480.0, {"front": 19.0, "back": 24.6})  # +12% -> pt-10
    _tick(conn, "2026-08-19", "11:00", 6470.0, {"front": 17.5, "back": 23.6})  # +22% -> pt-20
    _tick(conn, "2026-08-20", "11:00", 6460.0, {"front": 16.0, "back": 23.2})  # +44% -> pt-30; put touched
    _tick(conn, "2026-08-20", "15:50", 6462.0, {"front": 16.2, "back": 23.4})  # thu_close tick
    _tick(conn, "2026-08-21", "12:05", 6450.0, {"front": 10.0, "back": 21.0})  # fri_noon tick
    fri_close_marks = {"front": 5.0, "back": 20.0}
    _tick(conn, "2026-08-21", "15:50", 6445.0, fri_close_marks)  # the bell tick

    # Control closes for real at the bell tick's own quotes.
    for side in ("put", "call"):
        position = dict(
            conn.execute(
                "SELECT * FROM dc_positions WHERE position_id = ?", (f"2026-08-17:control:{side}",)
            ).fetchone()
        )
        assert book.close_open_legs(
            conn,
            position,
            _snapshot_from(fri_close_marks, 6445.0),
            {},
            reason="scheduled_exit",
            session_date="2026-08-21",
        )["ok"]

    # Path: shorts settle Friday, longs marked and disposed Monday.
    book.settle_expiring_legs(conn, "2026-08-21", SETTLE_SPOT, {}, symbol="SPX")
    mon_marks = {"back": 26.0}
    _tick(conn, "2026-08-24", "09:50", 6470.0, mon_marks, books=("path",))
    for side in ("put", "call"):
        position = dict(
            conn.execute(
                "SELECT * FROM dc_positions WHERE position_id = ?", (f"2026-08-17:path:{side}",)
            ).fetchone()
        )
        assert book.close_open_legs(
            conn,
            position,
            _snapshot_from(mon_marks, 6470.0),
            {},
            reason="long_disposition",
            session_date="2026-08-24",
        )["ok"]
    return conn


def _derive(week_conn, policy, book_name="path"):
    week = exit_policies.week_data(week_conn, WEEK["week_of"], book_name)
    return exit_policies.derive(week, policy, {})


def test_validation_is_clean_to_the_cent(week_conn):
    validation = exit_policies.validate_against_control(week_conn, {})
    assert validation["compared"] == 2
    assert validation["ok"], validation["mismatches"]


def test_profit_targets_trigger_on_successive_days(week_conn):
    assert _derive(week_conn, "pt-10")["trigger"]["session"] == "2026-08-18"
    assert _derive(week_conn, "pt-20")["trigger"]["session"] == "2026-08-19"
    assert _derive(week_conn, "pt-30")["trigger"]["session"] == "2026-08-20"


def test_stops_never_fire_and_fall_through_to_the_bell(week_conn):
    for policy in ("sl-25", "sl-50", "sl-100"):
        result = _derive(week_conn, policy)
        assert result["derivable"]
        assert result["trigger"]["reason"] == "fri_close"
        # Identical exit to the derived control, so identical net.
        assert result["net_pnl"] == _derive(week_conn, "control")["net_pnl"]


def test_derived_control_matches_the_arithmetic(week_conn):
    result = _derive(week_conn, "control")
    # Per share: shorts +15 each, longs -5 each -> +20/share -> $2000 gross.
    assert result["gross_pnl"] == pytest.approx(2000.0)
    assert result["net_pnl"] < result["gross_pnl"]  # fees are real


def test_touch_closes_the_touched_side_only(week_conn):
    result = _derive(week_conn, "touch-close-side")
    assert result["derivable"]
    assert result["trigger"]["side"] == "put"
    assert result["exits"]["front_put"]["session"] == "2026-08-20"
    assert result["exits"]["front_call"]["session"] == "2026-08-21"  # ran to the bell


def test_time_exits_take_their_own_ticks(week_conn):
    thu = _derive(week_conn, "time-thu-close")
    noon = _derive(week_conn, "time-fri-noon")
    assert thu["exits"]["front_put"]["session"] == "2026-08-20"
    assert noon["exits"]["front_put"]["session"] == "2026-08-21"
    assert thu["net_pnl"] != noon["net_pnl"]


def test_expiry_policies_settle_the_shorts(week_conn):
    fri = _derive(week_conn, "expiry-longs-fri")
    mon = _derive(week_conn, "expiry-longs-mon")
    for result in (fri, mon):
        assert result["derivable"]
        assert result["exits"]["front_put"]["kind"] == "cash_settled"
        assert result["exits"]["front_put"]["value"] == 25.0  # 6465 put at 6440
        assert result["exits"]["front_call"]["value"] == 0.0
    assert fri["exits"]["back_put"]["session"] == "2026-08-21"
    assert mon["exits"]["back_put"]["session"] == "2026-08-24"


def test_a_hole_in_the_path_is_not_derivable(week_conn):
    # Remove Friday's bell marks: control terminal has nothing to price.
    week_conn.execute(
        "DELETE FROM dc_marks WHERE session_date = '2026-08-21' AND position_id LIKE '%:path:%' "
        "AND marked_at > ?",
        (_ts("2026-08-21", "15:00"),),
    )
    week_conn.commit()
    result = _derive(week_conn, "control")
    assert result == {
        "week_of": WEEK["week_of"],
        "policy": "control",
        "structure": "dc_4_7",
        "derivable": False,
        "reason": "no_terminal_mark",
    }


def test_comparison_table_shape(week_conn):
    table = exit_policies.comparison_table(week_conn, {})
    assert table["weeks_considered"] == 1
    assert table["validation"]["ok"]
    pt10 = table["policies"]["pt-10"]["dc_4_7"]
    assert pt10["weeks"] == 1
    assert pt10["derivable"] == 1
    assert pt10["win_rate"] in (0.0, 1.0)
