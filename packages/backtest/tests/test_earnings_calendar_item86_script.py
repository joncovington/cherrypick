"""Item 86's script (earnings_calendar_item86.py): invented chains and bars only, the store never read."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

from cherrypick.core import calendar as cal

from cherrypick.backtest.study import rule as kit_rule
from cherrypick.backtest.study.chains import Chain

_spec = importlib.util.spec_from_file_location(
    "earnings_calendar_item86",
    Path(__file__).resolve().parents[1] / "scripts" / "earnings_calendar_item86.py",
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def test_the_rules_and_names_are_declared():
    assert kit_rule.check_rule(m.RULE) and m.RULE["delta"] is False and m.RULE["t_bar"] >= 2.24
    assert m.EVENT_RULE["t_bar"] == m.RULE["t_bar"] and m.EVENT_RULE["halves"] < m.RULE["splits"][0]
    assert all(len(ns) == 5 for ns in m.NAMES.values()) and len(m.SECTOR) == 35
    assert "GOOG" not in m.SECTOR and "META" not in m.SECTOR
    assert [s["step"] for s in m.CHAIN] == ["plan", "fetch-sample", "smoke", "fetch", "study"]


def test_the_front_is_a_weekly_near_three_weeks_and_the_back_a_monthly_near_fifteen():
    entry, reaction = date(2024, 4, 23), date(2024, 4, 24)
    d = m.trade_dates(entry, reaction)
    assert d["front"] == date(2024, 5, 17) and d["front"].weekday() == 4  # 24 days, nearest 21 on a Friday
    assert 84 <= (d["back"] - entry).days <= 126 and 15 <= d["back"].day <= 21
    assert d["last"] == m.sessions_after(reaction, 10)
    assert m.session_count(reaction, d["front"]) >= 7


def test_the_hold_ends_two_sessions_before_the_front_when_that_is_sooner():
    reaction, front = date(2024, 5, 1), date(2024, 5, 10)
    assert m.horizon(reaction, front) == m.sessions_before(front, 2)


def test_a_report_returned_when_any_close_in_the_ten_sessions_after_is_inside_the_pre_report_range():
    entry = date(2024, 4, 23)
    reaction = cal.next_trading_day(entry)
    bars, d = {}, entry
    for _ in range(10):
        bars[d] = (100.0, 105.0, 95.0, 100.0)
        d = cal.previous_trading_day(d)
    after = [m.sessions_after(reaction, i) for i in range(1, 11)]
    for x in after:
        bars[x] = (0, 0, 0, 110.0)  # every close above the range
    assert m.returned(bars, entry, reaction) is False
    bars[after[3]] = (0, 0, 0, 104.0)  # back inside on the fourth session only
    assert m.returned(bars, entry, reaction) is True
    assert m.returned(bars, entry, reaction, at_last_only=True) is False  # the first declaration's reading
    bars[after[-1]] = (0, 0, 0, 96.0)
    assert m.returned(bars, entry, reaction, at_last_only=True) is True
    assert m.returned(bars, entry, cal.next_trading_day(reaction)) is None  # a session after has no bar


def test_quiet_needs_eight_prior_reports_and_six_returns():
    assert m.quiet_status([True] * 7) is None
    assert m.quiet_status([False] * 4 + [True] * 6 + [False] * 2) is True  # the last eight: 6 of 8
    assert m.quiet_status([True] * 5 + [False] * 3) is False


def _chain(session: str, expiry: str, quotes: dict[float, tuple[float, float]]) -> Chain:
    rows = []
    for k, (b, a) in quotes.items():
        rows += [(k, "CALL", b, a), (k, "PUT", b, a)]
    return Chain(rows, session, expiry)


def _window():
    entry = date(2024, 4, 23)
    reaction = cal.next_trading_day(entry)
    return {
        "entry": entry,
        "reaction": reaction,
        "front": date(2024, 5, 17),
        "back": date(2024, 8, 16),
        "last": m.sessions_after(reaction, 10),
    }


def _marks(d: dict, spreads: list[float]) -> dict:
    """Front at 2.00, back at 2.00 + spread on each session from entry on."""
    out, day = {}, d["entry"]
    for s in spreads:
        out[day] = ({"bid": 1.95, "ask": 2.05}, {"bid": 1.95 + s, "ask": 2.05 + s})
        day = cal.next_trading_day(day)
    while day <= d["last"]:
        out[day] = out[cal.previous_trading_day(day)]
        day = cal.next_trading_day(day)
    return out


def _bars(d: dict, touch_on: date | None, strike: float = 100.0) -> dict:
    out, day = {}, d["entry"]
    while day <= d["last"]:
        out[day] = (110.0, 112.0, 108.0, 110.0) if day != touch_on else (101.0, 102.0, 99.0, 100.5)
        day = cal.next_trading_day(day)
    return out


def test_the_calendar_exits_on_the_first_session_that_trades_its_strike():
    d = _window()
    touch = m.sessions_after(d["reaction"], 3)
    t = m.price_trade("AAPL", "C", 100.0, _marks(d, [3.0, 3.5]), _bars(d, touch), d, stop_frac=0.25)
    assert t["why"] == "touch" and t["exit"] == touch.isoformat()
    assert t["paid"] > 300.0 and t["net"] == t["ret"] * t["paid"]


def test_the_stop_fires_at_a_quarter_off_the_entry_mid_and_the_entry_day_is_never_an_exit():
    d = _window()
    bars = _bars(d, d["entry"])  # a touch on the entry day does not count
    t = m.price_trade("AAPL", "C", 100.0, _marks(d, [3.0, 2.25]), bars, d, stop_frac=0.25)
    assert t["why"] == "stop" and t["exit"] == d["reaction"].isoformat()
    t = m.price_trade("AAPL", "C", 100.0, _marks(d, [3.0, 2.30]), bars, d, stop_frac=0.25)
    assert t["why"] == "time" and t["exit"] == d["last"].isoformat()


def test_an_exit_on_an_unquoted_session_waits_for_the_next_quote():
    d = _window()
    touch = m.sessions_after(d["reaction"], 2)
    marks = _marks(d, [3.0, 3.2])
    marks[touch] = None
    t = m.price_trade("AAPL", "C", 100.0, marks, _bars(d, touch), d, stop_frac=None)
    assert t["why"] == "touch" and t["exit"] == cal.next_trading_day(touch).isoformat()


def test_the_stress_costs_more_than_the_working_fill():
    d = _window()
    marks, bars = _marks(d, [3.0, 3.5]), _bars(d, None)
    a = m.price_trade("AAPL", "C", 100.0, marks, bars, d, stop_frac=None)
    b = m.price_trade("AAPL", "C", 100.0, marks, bars, d, stop_frac=None, stress=m.EVENT_RULE["stress"])
    assert b["costs"] > a["costs"] > 0 and b["net"] < a["net"]


def test_the_strike_is_the_one_nearest_the_target_quoted_in_both_expiries():
    f = _chain("2024-04-23", "2024-05-17", {95.0: (6.0, 6.2), 100.0: (2.0, 2.1), 105.0: (0.5, 0.6)})
    b = _chain("2024-04-23", "2024-08-16", {95.0: (9.0, 9.4), 105.0: (3.0, 3.2)})
    assert m.strike_near(f, b, "C", 101.0) == 105.0  # 100 has no back quote


def _trade(entry: str, ret: float, control: float | None, stress: float | None = None) -> dict:
    return {
        "entry": entry,
        "ret": ret,
        "control_ret": control,
        "stress_ret": ret if stress is None else stress,
        "net": ret * 300,
        "paid": 300.0,
        "days": 14,
    }


def test_the_event_rule_needs_the_pair_both_halves_the_stress_and_the_best_five():
    rule = {**m.EVENT_RULE}
    good = [
        _trade(f"202{y}-0{mo}-1{i % 9}", 0.05 + 0.001 * (i % 7), 0.0)
        for y in (0, 1, 2, 3)
        for mo in (2, 5, 8)
        for i in range(10)
    ]
    d = m.arm_part(good, rule)
    assert m.judge_event(d, d, rule, None, min_n=50, holdout_min_n=10)["verdict"] == m.PASS
    first_half_loses = [{**t, "ret": -0.05} if t["entry"] < rule["halves"] else t for t in good]
    d = m.arm_part(first_half_loses, rule)
    out = m.judge_event(d, d, rule, None, min_n=50, holdout_min_n=10)
    assert out["verdict"] == m.FAIL and out["checks"]["halves"] is False and out["holdout"] == m.NOT_READ
    d = m.arm_part(good, rule)
    out = m.judge_event(d, d, rule, 0.5, min_n=50, holdout_min_n=10)
    assert out["checks"]["beats_unselected"] is False
    assert (
        m.judge_event(m.arm_part(good[:10], rule), d, rule, None, min_n=50, holdout_min_n=10)["verdict"]
        == m.NO_VERDICT
    )


def test_an_arm_passes_only_on_both_rules():
    assert m.both(m.PASS, m.PASS) == m.PASS
    assert m.both(m.PASS, m.FAIL) == m.both(m.NO_VERDICT, m.FAIL) == m.FAIL
    assert m.both(m.PASS, m.NO_VERDICT) == m.NO_VERDICT


def test_the_requests_put_the_newest_events_first_and_each_path_once():
    def row(sym, entry, reaction):
        d = m.trade_dates(date.fromisoformat(entry), date.fromisoformat(reaction))
        return {
            "symbol": sym,
            "entry_session": entry,
            "dates": {k: v.isoformat() for k, v in d.items()},
            "control": None,
        }

    plan = [row("KO", "2021-04-19", "2021-04-20"), row("AAPL", "2024-04-23", "2024-04-24")]
    reqs = m.requests_of(plan)
    assert [r["symbol"] for r in reqs] == ["AAPL", "AAPL", "KO", "KO"]
    assert len({tuple(r.values()) for r in reqs}) == 4
