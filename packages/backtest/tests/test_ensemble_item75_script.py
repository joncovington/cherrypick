# ruff: noqa: E501
"""Hypothesis 75's study driver (packages/backtest/scripts/ensemble_item75.py) on invented stores: a few
sessions of Black-Scholes quotes written in the recorder's own tables. Each statistical bar is shown firing
and failing, because a check that cannot fire reads as coverage it is not."""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from cherrypick.core import bs
from cherrypick.ensemble.events import EventCalendar

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ensemble_item75.py"
ET = ZoneInfo("America/New_York")


def _module():
    spec = importlib.util.spec_from_file_location("ensemble_item75", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = _module()

DDL = (
    "CREATE TABLE gex_spot_history (symbol TEXT NOT NULL, trade_date TEXT NOT NULL, ts REAL NOT NULL, spot REAL NOT NULL)",
    "CREATE TABLE gex_profile_history (symbol TEXT NOT NULL, trade_date TEXT NOT NULL, ts REAL NOT NULL, strike REAL NOT NULL, call_oi REAL, put_oi REAL, call_vol REAL, put_vol REAL, call_gamma REAL, put_gamma REAL, call_iv REAL, put_iv REAL)",
    "CREATE TABLE gex_quote_samples (id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, trade_date TEXT NOT NULL, ts REAL NOT NULL, expiration TEXT NOT NULL)",
    "CREATE TABLE gex_quotes (sample_id INTEGER NOT NULL, side INTEGER NOT NULL, strike REAL NOT NULL, bid REAL NOT NULL, ask REAL NOT NULL, mid REAL NOT NULL, age_s REAL, delta REAL, PRIMARY KEY (sample_id, side, strike)) WITHOUT ROWID",
    "CREATE TABLE daily_closes (symbol TEXT NOT NULL, trade_date TEXT NOT NULL, close REAL NOT NULL, recorded_at REAL NOT NULL, source TEXT NOT NULL, PRIMARY KEY (symbol, trade_date))",
    "CREATE TABLE market_regime_history (trade_date TEXT NOT NULL, ts REAL NOT NULL, reading TEXT NOT NULL, symbol TEXT, value REAL, basis_ts REAL, usable INTEGER NOT NULL, reason TEXT)",
    "CREATE TABLE vendor_sessions (symbol TEXT NOT NULL, trade_date TEXT NOT NULL, status TEXT NOT NULL, reason TEXT, vendor TEXT NOT NULL, root TEXT NOT NULL, expiration TEXT, cadence_seconds REAL, samples INTEGER, quotes INTEGER, profiles INTEGER, fetched_at REAL NOT NULL, detail TEXT, PRIMARY KEY (symbol, trade_date))",
)


def ts(day: str, minute: int) -> float:
    return datetime.fromisoformat(day).replace(hour=minute // 60, minute=minute % 60, tzinfo=ET).timestamp()


def store(days: dict[str, float], closes: dict[str, float]) -> sqlite3.Connection:
    """`days` maps a session to its spot (flat all day); one 0DTE sample a minute from 09:30 to 16:00."""
    db = sqlite3.connect(":memory:")
    for stmt in DDL:
        db.execute(stmt)
    sid = 0
    for day, spot in days.items():
        db.execute(
            "INSERT INTO vendor_sessions VALUES ('SPX', ?, 'complete', NULL, 'v', 'SPXW', ?, 60, 0, 0, 0, 0, NULL)",
            (day, day),
        )
        for minute in range(570, 960):
            sid += 1
            years = max(960 - minute, 1) / (60 * 24 * 365)
            db.execute("INSERT INTO gex_spot_history VALUES ('SPX', ?, ?, ?)", (day, ts(day, minute), spot))
            db.execute(
                "INSERT INTO gex_quote_samples VALUES (?, 'SPX', ?, ?, ?)", (sid, day, ts(day, minute), day)
            )
            for k in range(int(spot) - 100, int(spot) + 101, 5):
                for side, right in ((0, "P"), (1, "C")):
                    p = bs.price(spot, k, years, 0.2, right, 0.0)
                    d = bs.delta(spot, k, years, 0.2, right, 0.0)
                    db.execute(
                        "INSERT INTO gex_quotes VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                        (sid, side, float(k), round(max(p - 0.1, 0), 2), round(p + 0.1, 2), round(p, 2), d),
                    )
            if minute % 5 == 0:
                for k in range(int(spot) - 100, int(spot) + 101, 5):
                    db.execute(
                        "INSERT INTO gex_profile_history VALUES ('SPX', ?, ?, ?, 1000, 1000, 0, 0, 0.003, 0.003, 0.2, 0.2)",
                        (day, ts(day, minute), float(k)),
                    )
            db.execute(
                "INSERT INTO market_regime_history VALUES (?, ?, 'vix', 'VIX', 16.0, NULL, 1, NULL)",
                (day, ts(day, minute)),
            )
    for day, c in closes.items():
        db.execute("INSERT INTO daily_closes VALUES ('SPX', ?, ?, 0, 'test')", (day, c))
    return db


def spx(days, closes, vix=None):
    return m.Spx(store(days, closes), vix or dict.fromkeys(closes, 16.0))


QUIET = EventCalendar(
    data={"CPI": [], "Sentiment": []},
    covered={"CPI": [("2000-01-01", "2100-01-01")], "Sentiment": [("2000-01-01", "2100-01-01")]},
)


def ctx(events=QUIET):
    return {"events": events, "entries": {}, "open": []}


def test_the_study_refuses_to_run_before_the_declaration_is_merged(capsys):
    assert m.DECLARATION_MERGED is False
    assert m.main(["study"]) == 2
    assert "not merged" in capsys.readouterr().err


def test_an_iron_fly_session_reads_the_store_and_settles_at_the_close():
    s = spx({"2026-11-13": 6000.0, "2026-11-16": 6000.0}, {"2026-11-13": 6000.0, "2026-11-16": 6000.0})
    arm = m.arm_mod.load_arms()["iron-fly-1000"]
    trades, why = m.run_zero_dte(arm, s, ctx(), ["2026-11-16"])
    assert why["entered"] == 1 and len(trades) == 1
    t = trades[0]
    assert t["exit_kind"] == "settlement" and t["session"] == "2026-11-16"
    for k in m.KINDS:  # the money identities hold on a real run through the reader
        assert t["net"][k] == pytest.approx(
            t["gross"] - t["fees"] - t["settlement"] - t["slippage"][k], abs=0.01
        )
    # spot closed on the short strikes: the fly keeps its whole credit
    assert t["gross"] == pytest.approx(t["entry_mid"] * 100 * t["quantity"], abs=0.01)


def test_a_session_the_arm_skips_is_counted_with_its_reason_and_unknown_events_are_flagged():
    days = {"2026-11-13": 6000.0, "2026-11-04": 6000.0, "2026-11-16": 6000.0}
    closes = dict.fromkeys(days, 6000.0) | {"2026-11-03": 6000.0}
    s = spx(days, closes)
    arm = m.arm_mod.load_arms()["iron-fly-1000"]
    trades, why = m.run_zero_dte(arm, s, ctx(), ["2026-11-04"])  # Wednesday: services PMI day
    assert trades == [] and why["event:PMI-Svcs"] == 1
    trades, why = m.run_zero_dte(arm, s, ctx(EventCalendar()), ["2026-11-16"])
    assert len(trades) == 1 and why["event_data_unknown"] == 1


def test_the_opening_range_arm_enters_once_and_a_touch_closes_it():
    days = {"2026-11-13": 6000.0, "2026-11-16": 6000.0}
    db = store(days, {"2026-11-13": 6000.0, "2026-11-16": 6000.0})
    day = "2026-11-16"
    # a break above the opening high at 10:40, then a fall of 12 points at 11:30
    db.execute("DELETE FROM gex_spot_history WHERE trade_date=?", (day,))
    for minute in range(570, 960):
        spot = 6000.0 if minute < 640 else (6008.0 if minute < 690 else 5995.0)
        db.execute("INSERT INTO gex_spot_history VALUES ('SPX', ?, ?, ?)", (day, ts(day, minute), spot))
    s = m.Spx(db, {"2026-11-13": 16.0, "2026-11-16": 16.0})
    arm = m.arm_mod.load_arms()["orb-put-spread"]
    trades, why = m.run_zero_dte(arm, s, ctx(), [day])
    assert why["entered"] == 1, dict(why)
    assert trades[0]["exit_kind"] == "touch" and trades[0]["exit_minute"] == 690
    flat = m.Spx(store(days, {"2026-11-13": 6000.0, "2026-11-16": 6000.0}), {"2026-11-13": 16.0})
    assert m.run_zero_dte(arm, flat, ctx(), [day])[1]["no_trigger"] == 1  # no break, no entry


def test_requests_cover_each_entry_and_exit_bar_once():
    files = m.request_files(["2026-11-16", "2026-11-17"])
    reqs = files["chains"]["requests"]
    keys = {(r["symbol"], r["session"], r["expiration"], r["start"], r["end"]) for r in reqs}
    assert len(keys) == len(reqs)
    assert ("SPXW", "2026-11-16", "2026-11-17", "13:30", "16:00") in keys
    assert ("GLD", "2026-11-16", "2026-11-17", "12:20", "12:20") in keys
    assert ("GLD", "2026-11-17", "2026-11-17", "15:45", "15:45") in keys
    gld20 = [r for r in reqs if r["symbol"] == "GLD" and r["start"] == "12:30"]
    assert gld20 and all(
        abs((datetime.fromisoformat(r["expiration"]) - datetime.fromisoformat(r["session"])).days - 20) <= 3
        for r in gld20
    )
    assert files["oi"] == {"sessions": {"2026-11-16": ["SPXW-1D"], "2026-11-17": ["SPXW-1D"]}}


def test_holm_and_the_bootstrap_bound_can_fail():
    assert m.holm({"a": 0.001, "b": 0.02, "c": None}) == {"a": True, "b": True, "c": False}
    assert m.holm({"a": 0.04, "b": 0.04}) == {"a": False, "b": False}  # 0.04 > 0.05 / 2
    rising = [5.0 + (i % 3) for i in range(120)]
    flat = [(-1.0) ** i for i in range(120)]
    assert m.bootstrap(rising)["lower95"] > 0
    assert m.bootstrap(flat)["lower95"] < 0


def _trades(nets, stress=None, fees=0.0, start="2022-05-16"):
    from datetime import date, timedelta

    out = []
    d = date.fromisoformat(start)
    for i, n in enumerate(nets):
        out.append(
            {
                "session": (d + timedelta(days=i)).isoformat(),
                "fees": fees,
                "settlement": 0.0,
                "net": {"verdict": n, "stress": n if stress is None else stress[i], "mid": n},
            }
        )
    return out


def test_the_pass_test_each_bar_blocks_on_its_own():
    good = [5.0 + (i % 3) for i in range(150)]
    assert m.judge_arm(_trades(good))["pre_pass"] is True
    assert m.judge_arm(_trades(good[:50]))["verdict"] == "no_verdict"
    one_half = [5.0 + (i % 3) for i in range(75)] + [-1.0 + (i % 3) * 0.1 for i in range(75)]
    assert m.judge_arm(_trades(one_half))["checks"]["both_halves"] is False
    assert m.judge_arm(_trades(good, stress=[-9.0] * 150))["checks"]["stress_fill"] is False
    thin = [1.0 + (i % 3) * 0.1 for i in range(150)]
    assert m.judge_arm(_trades(thin, fees=3.0))["checks"]["fees_x1_5"] is False  # 1.5x fees turns it negative
    zero_mean = [(-1.0) ** i for i in range(150)]
    assert m.judge_arm(_trades(zero_mean))["checks"]["bound_positive"] is False


def test_the_early_window_is_a_descriptive_row_only():
    t = _trades([3.0] * 10, start="2022-06-01") + _trades([1.0] * 10, start="2024-01-01")
    row = m.descriptive_row(t)
    assert row == {"sessions": 10, "mean_verdict": 3.0}


def test_the_portfolio_stacks_arms_by_session():
    a = _trades([5.0 + (i % 3) for i in range(120)])
    b = _trades([-1.0] * 120)
    p = m.portfolio({"a": a, "b": b})
    assert p["sessions"] == 120 and p["pre_pass"] is True
    bad = m.portfolio({"a": _trades([-2.0] * 120)})
    assert bad["pre_pass"] is False
