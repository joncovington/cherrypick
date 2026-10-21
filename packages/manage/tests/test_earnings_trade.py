"""Earnings trades: put on 1-7 days before a report to hold through it, closed after the call."""

import copy
import json
from datetime import date

from cherrypick.manage import __main__ as cli
from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules

RULES = _rules.DEFAULT_RULES
EXPIRY = "2026-10-23"


def leg(qty, right, strike, opened, mid, exp=EXPIRY):
    return {
        "symbol": f"XYZ   {exp.replace('-', '')[2:]}{right}{int(strike * 1000):08d}",
        "underlying": "XYZ",
        "instrument_type": "Equity Option",
        "quantity": qty,
        "expiration": exp,
        "right": right,
        "strike": strike,
        "open_price": opened,
        "mid": mid,
        "priced": True,
        "value": qty * mid * 100,
        "open_pl": qty * (mid - opened) * 100,
    }


# A losing call spread inside 21 days: the time exit says close -- unless it is an earnings trade.
LOSING = [leg(-1, "C", 192.5, 5.15, 5.72), leg(1, "C", 200, 2.36, 2.74)]


def chain(opened_on: str) -> dict:
    return {
        "cash": 279.0,
        "fees": -2.26,
        "orders": [
            {"executed_at": f"{opened_on}T15:00:00", "role": "open", "net": 279.0, "fees": -2.26, "legs": []}
        ],
    }


def run(
    session,
    report="2026-10-20",
    time_of_day=None,
    opened_on="2026-10-16",
    legs=LOSING,
    seen=None,
    rules=RULES,
):
    metrics = {"last": 190.0, "earnings_date": report, "earnings_time": time_of_day}
    c = chain(opened_on) if opened_on else None
    return ev.evaluate_trade("XYZ", copy.deepcopy(legs), metrics, session, 100_000.0, rules, c, seen)


def rec(t):
    return t["recommendation"]["action"], t["recommendation"]["rule"]


def test_before_the_call_an_earnings_trade_holds_and_its_close_waits():
    t = run(date(2026, 10, 18))
    assert t["earnings_trade"]["lead_days"] == 4 and not t["earnings_trade"]["passed"]
    assert rec(t) == (ev.HOLD, "earnings_trade")
    assert [f["rule"] for f in t["held_for_earnings"]] == ["credit.time_exit"]
    assert t["order"] is None
    assert "Hold it through the report" in t["explanation"] and "21-day exit" in t["explanation"]


def test_its_time_exit_is_the_day_after_the_call_not_expiry_less_21_days():
    # Expiry less 21 days would be 2026-10-02, before the trade was put on.
    assert run(date(2026, 10, 18))["exit_levels"]["time_exit"]["date"] == "2026-10-21"
    assert run(date(2026, 10, 18), time_of_day="BTO")["exit_levels"]["time_exit"]["date"] == "2026-10-20"
    # A Friday report closes the next trading day.
    later = [
        leg(-1, "C", 192.5, 5.15, 5.72, exp="2026-10-30"),
        leg(1, "C", 200, 2.36, 2.74, exp="2026-10-30"),
    ]
    friday = run(date(2026, 10, 18), report="2026-10-23", legs=later)
    assert friday["exit_levels"]["time_exit"]["date"] == "2026-10-26"
    # Not an earnings trade (opened too early): the 21-day exit stands.
    assert run(date(2026, 10, 18), opened_on="2026-10-01")["exit_levels"]["time_exit"]["date"] == "2026-10-02"


def test_after_the_call_it_closes():
    t = run(date(2026, 10, 21))
    assert t["earnings_trade"]["passed"]
    assert ev.CLOSE == t["recommendation"]["action"]
    assert "earnings_trade" in {f["rule"] for f in t["fired"]}
    assert t["order"] is not None


def test_a_report_before_the_open_has_passed_on_its_own_day_one_after_the_close_has_not():
    assert run(date(2026, 10, 20), time_of_day="BTO")["earnings_trade"]["passed"]
    assert not run(date(2026, 10, 20), time_of_day="AMC")["earnings_trade"]["passed"]
    # BMO is the same as the broker's BTO: closed on the report's own day.
    bmo = run(date(2026, 10, 20), time_of_day="bmo")
    assert bmo["earnings_trade"]["passed"] and bmo["exit_levels"]["time_exit"]["date"] == "2026-10-20"
    assert rec(bmo)[0] == ev.CLOSE


def test_a_blank_time_carried_from_yesterday_takes_todays_for_the_same_report():
    # Yesterday's evaluation had no time; today's snapshot has one for the same date: it is used, so a
    # before-the-open report closes on its day rather than the next.
    blank = {f"XYZ|{EXPIRY}": {"report": "2026-10-20", "time": None, "opened": "2026-10-16"}}
    t = run(date(2026, 10, 20), time_of_day="BTO", seen=blank)
    assert t["earnings_trade"]["time_of_day"] == "BTO" and t["earnings_trade"]["passed"]
    # A time for a different report date (next quarter's) is never borrowed.
    assert (
        run(date(2026, 10, 20), report="2027-01-20", time_of_day="BTO", seen=blank)["earnings_trade"][
            "time_of_day"
        ]
        is None
    )
    # The broker correcting yesterday's time for the same report is taken; a blank today keeps it.
    timed = {f"XYZ|{EXPIRY}": {"report": "2026-10-20", "time": "AMC", "opened": "2026-10-16"}}
    corrected = run(date(2026, 10, 20), time_of_day="BTO", seen=timed)
    assert corrected["earnings_trade"]["time_of_day"] == "BTO" and corrected["earnings_trade"]["passed"]
    assert run(date(2026, 10, 20), time_of_day=None, seen=timed)["earnings_trade"]["time_of_day"] == "AMC"


SEEN = {f"XYZ|{EXPIRY}": {"report": "2026-10-20", "time": "AMC", "opened": "2026-10-16"}}


def test_a_rescheduled_report_moves_the_trade_with_it():
    # Recorded for 10-20; the broker now says 10-21. On 10-21 the call is still ahead: hold, not close.
    t = run(date(2026, 10, 21), report="2026-10-21", time_of_day="AMC", seen=SEEN)
    assert t["earnings_trade"]["report"] == "2026-10-21" and not t["earnings_trade"]["passed"]
    assert rec(t) == (ev.HOLD, "earnings_trade")
    # Moved to 10-22 after the close, it would come off on expiry day: the expiry close stands, but
    # not the earnings trade's own close.
    late = run(date(2026, 10, 21), report="2026-10-22", time_of_day="AMC", seen=SEEN)
    assert not late["earnings_trade"]["passed"] and rec(late) == (ev.CLOSE, "expiry")
    assert "earnings_trade" not in {f["rule"] for f in late["fired"]}
    # Next quarter's date (past the short's expiry) is not a reschedule: the record stands.
    rolled = run(date(2026, 10, 21), report="2027-01-20", seen=SEEN)
    assert rolled["earnings_trade"]["report"] == "2026-10-20" and rolled["earnings_trade"]["passed"]


def test_a_report_on_the_shorts_expiry_day_counts_only_before_the_open():
    friday = "2026-10-23"
    for when in ("AMC", None):
        t = run(date(2026, 10, 21), report=friday, time_of_day=when, opened_on="2026-10-19")
        assert t["earnings_trade"] is None, when
    bto = run(date(2026, 10, 21), report=friday, time_of_day="BTO", opened_on="2026-10-19")
    assert bto["earnings_trade"]["report"] == friday


DIAGONAL_LOSING = [leg(-1, "C", 192.5, 5.15, 5.72), leg(1, "C", 200, 6.00, 5.00, exp="2026-12-18")]
DIAGONAL_WINNING = [leg(-1, "C", 192.5, 5.15, 1.00), leg(1, "C", 200, 6.00, 6.50, exp="2026-12-18")]


def test_after_the_call_a_losing_trade_with_a_later_expiry_is_kept_for_the_ordinary_rules():
    lose = run(date(2026, 10, 21), legs=DIAGONAL_LOSING, opened_on=None, seen=SEEN)
    assert lose["pnl_gross"] < 0
    assert lose["earnings_trade"]["passed"] and lose["earnings_trade"]["kept"]
    assert "earnings_trade" not in {f["rule"] for f in lose["fired"]}
    assert "ordinary rules manage it" in lose["explanation"]
    # A winner with the same shape closes after the call.
    win = run(date(2026, 10, 21), legs=DIAGONAL_WINNING, opened_on=None, seen=SEEN)
    assert win["pnl_gross"] > 0 and not win["earnings_trade"]["kept"]
    assert "earnings_trade" in {f["rule"] for f in win["fired"]}
    # A losing single-expiry spread still closes after the call.
    single = run(date(2026, 10, 21))
    assert single["pnl_gross"] < 0 and "earnings_trade" in {f["rule"] for f in single["fired"]}


def test_a_report_before_todays_open_is_no_longer_inside_an_ordinary_trade():
    far = [leg(-1, "C", 192.5, 5.15, 4.00, exp="2026-11-20"), leg(1, "C", 200, 2.36, 1.80, exp="2026-11-20")]
    out = run(date(2026, 10, 20), time_of_day="BTO", opened_on="2026-09-20", legs=far)
    assert out["earnings"] is None and "inside the trade" not in out["recommendation"]["reason"]
    ahead = run(date(2026, 10, 20), time_of_day="AMC", opened_on="2026-09-20", legs=far)
    assert ahead["earnings"]["date"] == "2026-10-20"


def test_only_a_trade_put_on_up_to_seven_days_before_the_report_is_one():
    # 1 and 7 days before are in; 8 days are not.
    for opened_on, expect in (
        ("2026-10-19", True),
        ("2026-10-13", True),
        ("2026-10-12", False),
    ):
        t = run(date(2026, 10, 18), opened_on=opened_on)
        assert (t["earnings_trade"] is not None) is expect, opened_on
    eight = run(date(2026, 10, 18), opened_on="2026-10-12")
    assert rec(eight) == (ev.CLOSE, "credit.time_exit")  # not an earnings trade: the time exit stands


def test_the_reports_own_day_counts_only_before_a_call_after_the_close():
    # Opened the morning of an after-the-close report, it was put on for that call.
    t = run(date(2026, 10, 20), time_of_day="AMC", opened_on="2026-10-20")
    assert t["earnings_trade"]["lead_days"] == 0 and not t["earnings_trade"]["passed"]
    # A before-the-open call had already happened when the trade went on: not put on for it.
    assert run(date(2026, 10, 20), time_of_day="BTO", opened_on="2026-10-20")["earnings_trade"] is None


def test_the_expiry_close_waits_when_the_trade_comes_off_after_the_call_before_expiry():
    # The report tonight after the close, the short expiring Friday (3 DTE): the expiry window is
    # reached, but the trade closes tomorrow, before expiry, so the expiry close waits with the rest.
    t = run(date(2026, 10, 20), time_of_day="AMC")
    assert rec(t) == (ev.HOLD, "earnings_trade")
    assert [f["rule"] for f in t["held_for_earnings"]] == ["expiry", "credit.time_exit"]
    assert t["order"] is None
    assert "expiry rule" in t["explanation"]
    # Tomorrow the call is out: the trade closes, and the expiry close stands beside it.
    after = run(date(2026, 10, 21), time_of_day="AMC")
    assert ev.CLOSE == after["recommendation"]["action"]
    assert {"earnings_trade", "expiry"} <= {f["rule"] for f in after["fired"]}


def test_a_short_expiring_long_after_the_report_is_not_an_earnings_trade():
    far = [leg(-1, "C", 192.5, 5.15, 5.72, exp="2026-11-20"), leg(1, "C", 200, 2.36, 2.74, exp="2026-11-20")]
    assert run(date(2026, 10, 18), legs=far)["earnings_trade"] is None


def test_without_an_order_chain_there_is_no_opening_date_and_no_classification():
    t = run(date(2026, 10, 18), opened_on=None)
    assert t["earnings_trade"] is None and rec(t) == (ev.CLOSE, "credit.time_exit")


def test_the_expiry_close_stands_when_the_trade_would_come_off_on_expiry_day():
    # Report tomorrow (untimed, so closed the day after), physically settled short expiring that same
    # day: the post-call close would be on expiry day, so the expiry rule still closes it now.
    near = [leg(-1, "C", 192.5, 5.15, 5.72, exp="2026-10-20"), leg(1, "C", 200, 2.36, 2.74, exp="2026-10-20")]
    t = run(date(2026, 10, 18), report="2026-10-19", opened_on="2026-10-16", legs=near)
    assert t["earnings_trade"] is not None
    assert rec(t) == (ev.CLOSE, "expiry")


def test_the_record_carries_a_trade_past_its_report_when_the_broker_moves_on():
    # The day after, the broker shows next quarter's date; yesterday's record still knows the trade.
    seen = {f"XYZ|{EXPIRY}": {"report": "2026-10-20", "time": None, "opened": "2026-10-16"}}
    t = run(date(2026, 10, 21), report="2027-01-20", seen=seen)
    assert t["earnings_trade"]["report"] == "2026-10-20" and t["earnings_trade"]["passed"]
    assert "earnings_trade" in {f["rule"] for f in t["fired"]}
    assert run(date(2026, 10, 21), report="2027-01-20")["earnings_trade"] is None  # without it, forgotten


def test_the_switch_and_a_per_name_override_restore_the_ordinary_rules():
    off, _ = _rules.effective({"earnings_trade": {"enabled": False}})
    t = run(date(2026, 10, 18), rules=off)
    assert rec(t) == (ev.CLOSE, "credit.time_exit")
    assert t["earnings_trade"] is None
    excused, _ = _rules.effective({"positions": {"XYZ": {"disable": ["earnings_trade"]}}})
    t = run(date(2026, 10, 18), rules=excused)
    assert rec(t) == (ev.CLOSE, "credit.time_exit")
    # Excused, it is not an earnings trade: a reader (the console's held-for-call chip) must not be
    # told it is holding for a call while its ordinary exit closes it.
    assert t["earnings_trade"] is None
    assert t["exit_levels"]["time_exit"].get("rule") != "earnings_trade"
    # Another name's opt-out excuses nothing here.
    other, _ = _rules.effective({"positions": {"ABC": {"disable": ["earnings_trade"]}}})
    assert run(date(2026, 10, 18), rules=other)["earnings_trade"]["report"] == "2026-10-20"


def test_the_evaluate_command_carries_yesterdays_earnings_trades_and_drops_expired_ones(tmp_path):
    doc = {
        "accounts": [
            {
                "trades": [
                    {
                        "earnings_trade": {
                            "key": "XYZ|2026-10-23",
                            "report": "2026-10-20",
                            "time_of_day": "AMC",
                            "opened": "2026-10-16",
                        }
                    },
                    {
                        "earnings_trade": {
                            "key": "OLD|2026-10-16",
                            "report": "2026-10-14",
                            "time_of_day": None,
                            "opened": "2026-10-10",
                        }
                    },
                    {"earnings_trade": None},
                ]
            }
        ]
    }
    (tmp_path / "eval-2026-10-20.json").write_text(json.dumps(doc), encoding="utf-8")
    (tmp_path / "eval-2026-10-22.json").write_text(
        json.dumps({"accounts": []}), encoding="utf-8"
    )  # later: not read
    seen = cli.earnings_trades_seen("2026-10-21", tmp_path)
    assert seen == {"XYZ|2026-10-23": {"report": "2026-10-20", "time": "AMC", "opened": "2026-10-16"}}
    assert cli.earnings_trades_seen("2026-10-21", tmp_path / "none") == {}
    # A failed day in between (no trades read, or an unreadable file) is passed over, not taken as
    # "no earnings trades".
    failed = tmp_path / "eval-2026-10-21.json"
    failed.write_text(json.dumps({"ok": False, "accounts": []}), encoding="utf-8")
    assert cli.earnings_trades_seen("2026-10-22", tmp_path) == seen
    failed.write_text("{broken", encoding="utf-8")
    assert cli.earnings_trades_seen("2026-10-22", tmp_path) == seen
