"""Early-close ("half") days: every exit clock is an offset from that day's close, and no entry scan runs.

The loop's clock used to be fixed at a 16:00 close, so on a 13:00 close it ran its expiry close, pin
guard and management window against a market that had already shut, and scanned for entries at 15:35
on chains nobody could trade. 2026-11-27 (the Friday after Thanksgiving, a weekly-expiry day) is the
first one after this change; 2026-12-24 is the next.
"""

import argparse
import json
from datetime import date, datetime

import pytest

from cherrypick.earnings import db_paper, management, paper_loop, session_clock
from cherrypick.earnings import strat_test_harness as harness
from cherrypick.earnings import strategy_metrics as metrics

ET = management.ET
HALF = "2026-11-27"
NORMAL = "2026-08-12"
CONFIG = {"strategies": {}, "management": {}, "tastytrade_costs": {}}


def at(hhmm, day=HALF):
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00").replace(tzinfo=ET)


def _ns(**kwargs):
    return argparse.Namespace(**kwargs)


# An iron fly expiring on the half day itself, short the 190 straddle.
LEGS = [
    {"symbol": "AAPL  261127C00190000", "action": "Sell to Open", "quantity": 1},
    {"symbol": "AAPL  261127P00190000", "action": "Sell to Open", "quantity": 1},
    {"symbol": "AAPL  261127C00200000", "action": "Buy to Open", "quantity": 1},
    {"symbol": "AAPL  261127P00180000", "action": "Buy to Open", "quantity": 1},
]
TRADE = {
    "order_id": "T1",
    "strategy": "iron_fly",
    "symbol": "AAPL",
    "expiration": HALF,
    "entry_credit": 5.0,
    "legs_json": json.dumps(LEGS),
    "opened_at": None,
}


def _decide(now, *, spot=190.4):
    snap = {"ok": True, "quotes": {}, "spot": spot, "max_spread_pct": 0.05, "source": "stream"}
    return management.evaluate(
        dict(TRADE), snap, {"strategies": {}, "management": {}}, now=now, sessions_held=1
    )


@pytest.fixture
def wired(tmp_path, monkeypatch):
    book = tmp_path / "paper_trades.db"
    monkeypatch.setattr(db_paper, "DB_PATH", book)
    monkeypatch.setattr(metrics, "DB_PATH", book)
    monkeypatch.setattr(metrics, "PAPER_DB_PATH", book)
    db_paper.cmd_init_db(argparse.Namespace())
    monkeypatch.setattr(paper_loop, "state_file", lambda name: tmp_path / name)
    monkeypatch.setattr(paper_loop, "lock_path", lambda: tmp_path / "loop.lock")
    monkeypatch.setattr(paper_loop.stream_request, "register", lambda *a, **k: None)
    logs = []
    monkeypatch.setattr(paper_loop, "append_run_log", logs.append)
    return tmp_path, logs


# --------------------------------------------------------------------------- the clock itself
def test_a_normal_day_keeps_the_old_times():
    c = session_clock.for_date(date.fromisoformat(NORMAL))
    assert (c.half_day, c.close, c.expiry_close, c.management_end) == (False, 960, 930, 940)
    assert (c.eod_start, c.eod_end) == (960, 990)


def test_the_half_day_shifts_every_time_by_the_same_three_hours():
    c = session_clock.for_date(date.fromisoformat(HALF))
    assert (c.half_day, c.close, c.expiry_close, c.management_end) == (True, 780, 750, 760)
    assert (c.eod_start, c.eod_end) == (780, 810)


# --------------------------------------------------------------------------- expiry close and pin guard
def test_the_expiry_close_fires_at_half_past_twelve_on_a_half_day():
    assert _decide(at("12:25"), spot=250.0).action == "hold"
    d = _decide(at("12:45"), spot=250.0)
    assert d.action == "close_all" and d.reason == "expiry_close"


def test_the_expiry_close_still_waits_for_15_30_on_a_normal_day():
    legs = json.loads(TRADE["legs_json"])
    normal_legs = [dict(leg, symbol=leg["symbol"].replace("261127", "260812")) for leg in legs]
    trade = dict(TRADE, expiration=NORMAL, legs_json=json.dumps(normal_legs))
    snap = {"ok": True, "quotes": {}, "spot": 300.0, "max_spread_pct": 0.05, "source": "stream"}
    cfg = {"strategies": {}, "management": {}}
    assert management.evaluate(trade, snap, cfg, now=at("12:45", NORMAL), sessions_held=1).reason != (
        "expiry_close"
    )
    d = management.evaluate(trade, snap, cfg, now=at("15:30", NORMAL), sessions_held=1)
    assert d.reason == "expiry_close"


def test_the_pin_guard_window_runs_to_the_half_day_close():
    # 12:15 is 45 minutes before the 13:00 close: inside the hour, short strike within a dollar.
    d = _decide(at("12:15"))
    assert d.action == "close_all" and d.reason == "pin_risk"
    # 11:30 is 90 minutes before the close: outside the hour.
    assert _decide(at("11:30")).action != "close_all"


# --------------------------------------------------------------------------- the phases
@pytest.mark.parametrize(
    "hhmm, phase",
    [
        ("09:45", "management"),
        ("12:35", "management"),
        ("12:39", "management"),
        ("12:40", "off_hours"),
        ("12:59", "off_hours"),
        ("13:00", "eod"),
        ("13:29", "eod"),
        ("13:30", "off_hours"),
        ("15:30", "off_hours"),
        ("15:35", "off_hours"),
        ("15:50", "off_hours"),
    ],
)
def test_the_half_day_phases_follow_the_early_close(hhmm, phase):
    assert paper_loop.phase_for(at(hhmm), CONFIG, entry_done=False) == phase


def test_a_normal_day_keeps_its_phases():
    for hhmm, phase in [("15:35", "entry"), ("15:39", "entry"), ("15:56", "off_hours"), ("16:00", "eod")]:
        got = paper_loop.phase_for(at(hhmm, NORMAL), CONFIG, entry_done=False)
        assert got == phase, hhmm
    assert paper_loop.phase_for(at("15:45", NORMAL), CONFIG, entry_done=True) == "off_hours"
    assert paper_loop.phase_for(at("15:30", NORMAL), CONFIG, entry_done=True) == "management"


def test_no_entry_scan_runs_on_a_half_day(monkeypatch, wired):
    calls = []
    monkeypatch.setattr(harness, "cmd_run_entries", lambda args: calls.append(args.date) or {"ok": True})
    monkeypatch.setattr(paper_loop, "manage", lambda *a, **k: {"ok": True})
    for hhmm in ("12:35", "15:35", "15:50"):
        assert paper_loop.run_iteration(CONFIG, at(hhmm))["phase"] != "entry"
    assert calls == []


def test_a_half_day_says_plainly_that_it_took_no_entries(wired):
    tmp, logs = wired
    first = paper_loop.run_iteration(CONFIG, at("13:01"))
    paper_loop.run_iteration(CONFIG, at("13:02"))
    record = json.loads((tmp / "earnings_entry.last.json").read_text(encoding="utf-8"))
    assert record["date"] == HALF and record["ok"] is True
    assert record["reason"] == "half_day_no_entries" and not record["opened"]
    assert first["entry"]["reason"] == "half_day_no_entries"
    assert [r.get("reason") for r in logs].count("half_day_no_entries") == 1, "once, not every tick"


# --------------------------------------------------------------------------- the harness deadline
def _clock(monkeypatch, hhmm, day):
    monkeypatch.setattr(harness, "_now_et", lambda: at(hhmm, day))


def test_the_harness_opens_nothing_on_a_half_day(monkeypatch):
    _clock(monkeypatch, "12:35", HALF)
    assert harness._past_entry_window({"entry_window_end": "15:55"}) is True
    assert harness._past_entry_window({}) is True


def test_the_harness_deadline_is_unchanged_on_a_normal_day(monkeypatch):
    _clock(monkeypatch, "15:54", NORMAL)
    assert harness._past_entry_window({"entry_window_end": "15:55"}) is False
    _clock(monkeypatch, "15:55", NORMAL)
    assert harness._past_entry_window({"entry_window_end": "15:55"}) is True
