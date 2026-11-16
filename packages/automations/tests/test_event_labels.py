"""The event labels added from the reference catalog: triple witching, the VIX expiration and the futures
roll by rule; PPI, NFP and PCE from the calendar store; each symbol's earnings and ex-dividend dates as
its own. A data label is unknown wherever no source speaks for it."""

from __future__ import annotations

import copy
import json
from datetime import date

import pytest
from cherrypick.core import events as core_events
from market import spx_snapshot

from cherrypick.automations import arms, engine, events

D = date.fromisoformat


@pytest.mark.parametrize(
    ("label", "year", "month", "want"),
    [
        ("TripleWitching", 2026, 3, ["2026-03-20"]),
        ("TripleWitching", 2026, 6, ["2026-06-18"]),  # the 19th is Juneteenth: the Thursday
        ("TripleWitching", 2026, 5, []),
        ("FuturesRoll", 2026, 3, ["2026-03-12"]),
        ("FuturesRoll", 2026, 6, ["2026-06-11"]),
        ("FuturesRoll", 2026, 4, []),
        ("VIXExpiry", 2026, 1, ["2026-01-21"]),
        ("VIXExpiry", 2026, 11, ["2026-11-18"]),
        ("VIXExpiry", 2025, 3, ["2025-03-18"]),  # April's third Friday was Good Friday: a Tuesday
    ],
)
def test_the_rule_labels_fall_where_the_exchanges_put_them(label, year, month, want):
    assert [d.isoformat() for d in events.rule_days(label, year, month)] == want


def test_the_vix_expiration_falls_once_a_month_on_a_trading_day():
    for year in (2024, 2025, 2026):
        for month in range(1, 13):
            (d,) = events.rule_days("VIXExpiry", year, month)
            assert events.cal.is_trading_day(d) and d.weekday() <= 2


def calendar_store(tmp_path):
    cal = tmp_path / "calendar"
    cal.mkdir()
    (cal / "bea.json").write_text(
        json.dumps({"Personal Income and Outlays": {"release_dates": ["2026-10-30T12:30:00+00:00"]}}),
        encoding="utf-8",
    )
    rows = [
        {"name": "Producer Price Index", "at": "2026-11-13"},
        {"name": "Employment Situation", "at": "2026-11-06"},
    ]
    doc = core_events.merge_fred_history(None, rows, "2026-10-01", "2026-12-31")
    (cal / "fred_history.json").write_text(json.dumps(doc), encoding="utf-8")
    sym = tmp_path / "symbol_events.json"
    sym.write_text(
        json.dumps(
            {
                "labels": {
                    "Earnings": {"SPY": {"dates": ["2026-11-16"], "covered": [["2026-01-01", "2026-12-31"]]}},
                    "ExDividend": {
                        "SPX": {"dates": ["2026-11-16"], "covered": [["2011-01-03", "2026-11-30"]]}
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    return cal, sym


def test_the_store_labels_answer_where_the_store_covers(tmp_path):
    cal, sym = calendar_store(tmp_path)
    c = events.load_calendar(tmp_path / "no-item75.json", store_root=cal, symbol_file=sym)
    assert c.occurs("PPI", D("2026-11-13"), D("2026-11-13")) is True
    assert c.occurs("PPI", D("2026-11-12"), D("2026-11-12")) is False
    assert c.occurs("PPI", D("2026-09-12"), D("2026-09-12")) is None  # before FRED's coverage
    assert c.occurs("NFP", D("2026-11-06"), D("2026-11-06")) is True
    assert c.occurs("PCE", D("2026-10-30"), D("2026-10-30")) is True
    # A label the item 75 file carries keeps that file's dates.
    item75 = tmp_path / "item75.json"
    item75.write_text(json.dumps({"data": {"PPI": []}, "covered": {"PPI": [["2026-01-01", "2026-12-31"]]}}))
    kept = events.load_calendar(item75, store_root=cal, symbol_file=sym)
    assert kept.occurs("PPI", D("2026-11-13"), D("2026-11-13")) is False


def test_a_symbol_label_is_the_symbols_own(tmp_path):
    cal, sym = calendar_store(tmp_path)
    c = events.load_calendar(tmp_path / "none.json", store_root=cal, symbol_file=sym)
    day = D("2026-11-16")
    assert c.occurs("Earnings", day, day, "spy") is True
    assert c.occurs("Earnings", D("2026-11-17"), D("2026-11-17"), "SPY") is False
    assert c.occurs("Earnings", day, day, "QQQ") is None  # no dates for it: unknown
    assert c.dates("Earnings", day, day, "SPY") == ["2026-11-16"]
    assert c.occurs("Earnings", day, day) is None  # a symbol label read without a symbol is unknown


def test_the_engine_reads_a_symbol_label_for_the_arms_own_symbol(tmp_path):
    cal, sym = calendar_store(tmp_path)
    c = events.load_calendar(tmp_path / "none.json", store_root=cal, symbol_file=sym)
    arm = copy.deepcopy(arms.load_arms()["iron-fly-1000"])
    arm["skip_events"] = {"labels": ["ExDividend"], "through_expiry": False}
    snap = spx_snapshot()
    snap.events = c
    assert engine.decide(arm, snap).reason == "event:ExDividend"
    arm["skip_events"] = {"labels": ["Earnings"], "through_expiry": False}
    d = engine.decide(arm, snap)  # SPY's earnings are not SPX's: unknown, passed and said so
    assert d.enter and ("event:Earnings", True, "unknown") in d.checks
    trigger = {
        "signal": {
            "kind": "event",
            "event": "ExDividend",
            "timing": "on",
            "days": 0,
            "unit": "sessions",
            "at": "10:00",
        }
    }
    assert engine.events_for(trigger, snap)["ExDividend"] == ["2026-11-16"]
