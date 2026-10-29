"""The fair-value build (valuation.py): one valued stock, one fund, one bank and one refused name from
a temp history store, and the file it writes."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, timedelta

import pytest

from cherrypick.technicals import paths, statements, store, valuation

SESSION = "2026-10-27"


def _bars(conn, symbol: str, start: float, drift: float, wobble: float, days: int = 900) -> None:
    """Daily closes back from the session: a geometric path with a deterministic wobble."""
    d0 = date.fromisoformat(SESSION) - timedelta(days=days)
    rows = []
    px = start
    k = 0
    for i in range(days + 1):
        day = d0 + timedelta(days=i)
        if day.weekday() >= 5:
            continue
        px *= math.exp(drift + wobble * math.sin(k))
        k += 1
        rows.append((symbol, day.isoformat(), px, px, px, px, 1000.0))
    conn.executemany("INSERT OR REPLACE INTO bars VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def _statements(conn, symbol: str, years: int = 5, ebit_margin: float = 0.10) -> None:
    rows = []
    for k in range(years):
        end = f"{2021 + k}-12-31"
        rev = 1000.0 * 1.05**k
        rows.append(
            (symbol, end, rev, rev * ebit_margin, rev * 0.05, rev * 0.09, rev * 0.09 * 0.25, 10.0, rev * 0.06,
             rev * 0.14, rev * 0.05, -rev * 0.01, 200.0, 0.0, 100.0, 100.0, 500.0)
        )  # fmt: skip
    conn.executemany(
        f"INSERT OR REPLACE INTO statements_annual VALUES ({', '.join('?' for _ in statements.COLUMNS)})",
        rows,
    )


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setenv("TECHNICALS_DATA_DIR", str(tmp_path))
    db = paths.history_db()
    conn = store.connect(db)
    conn.executescript(statements.SCHEMA)
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS estimates (symbol TEXT, date TEXT, kind TEXT, period_end TEXT,"
        " consensus REAL, year_ago REAL, PRIMARY KEY (symbol, date, kind));"
    )
    _bars(conn, "SPY", 400.0, 0.0003, 0.01)
    _bars(conn, "AAA", 50.0, 0.0004, 0.02)  # moves with the index, about twice as much
    _bars(conn, "BNK", 30.0, 0.0003, 0.01)
    _bars(conn, "FND", 80.0, 0.0003, 0.01)
    _bars(conn, "LOSS", 10.0, 0.0003, 0.01)
    _bars(conn, "NEW", 20.0, 0.0003, 0.01)
    _statements(conn, "AAA")
    _statements(conn, "BNK")
    _statements(conn, "LOSS", ebit_margin=-0.05)
    conn.execute("INSERT INTO estimates VALUES ('AAA', '2026-10-25', 'sales', '2027-12-31', 1300.0, 1215.5)")
    conn.commit()
    conn.close()
    # Stand in for the security list, the sector maps and the broker file: local files only.
    monkeypatch.setattr(valuation.securities, "latest", lambda day=None: ([
        {"symbol": "AAA", "type": "Stock"},
        {"symbol": "BNK", "type": "Stock"},
        {"symbol": "FND", "type": "Exchange Traded Fund"},
        {"symbol": "LOSS", "type": "Stock"},
        {"symbol": "NEW", "type": "Stock"},
    ], {"day": SESSION, "source": "public"}))  # fmt: skip
    monkeypatch.setattr(
        valuation.fundamentals,
        "sector_sources",
        lambda: {"AAA": ("Technology", "yahoo"), "BNK": ("Financials", "yahoo")},
    )
    monkeypatch.setattr(
        valuation,
        "broker_readings",
        lambda as_of: {"AAA": {"beta": 1.1, "market_cap": 5.0e3}, "NEW": {"beta": 0.9, "market_cap": 1.0}},
    )
    return db


def test_the_build_values_a_stock_and_says_why_the_others_have_no_value(history):
    out = valuation.build(SESSION, names=["AAA", "BNK", "FND", "LOSS", "NEW"])
    assert out["ok"], out
    doc = json.loads(valuation.latest(SESSION).read_text(encoding="utf-8"))
    rows = doc["rows"]
    assert doc["version"] == valuation.VERSION and doc["session"] == SESSION
    assert rows["FND"]["state"] == "not-applicable" and rows["FND"]["reason"] == "fund"
    assert rows["BNK"]["state"] == "not-applicable" and rows["BNK"]["reason"] == "financials"
    assert rows["LOSS"]["state"] == "refused" and rows["LOSS"]["reason"] == "no_operating_profit"
    assert rows["NEW"]["state"] == "no-statements"
    a = rows["AAA"]
    assert a["state"] == "valued"
    assert a["fair_value"] > 0 and a["low"] <= a["fair_value"] <= a["high"]
    assert a["gap"] == pytest.approx(a["spot"] / a["fair_value"] - 1, rel=1e-2)  # fair_value is rounded
    assert a["consensus_used"] is True
    assert a["growth_y1"] == pytest.approx((1300.0 - 1215.5) / 1215.5, abs=1e-4)
    assert a["beta_raw"] == pytest.approx(2.0, abs=0.3)  # twice the index's wobble, same sign
    assert a["beta_broker"] == 1.1 and "beta_disagrees" in a["flags"]
    assert a["market_cap"] == pytest.approx(a["spot"] * 100.0)
    assert "shares_stale" in a["flags"]  # the broker says 5,000 against spot x 100 shares
    assert "valued" in doc["states"] and doc["rule"]["edge"].startswith("none measured")
    # The tab's bridge and forecast are the base case's own arithmetic: they add up to the value.
    b = a["bridge"]
    assert b["pv_forecast"] + b["pv_terminal"] == pytest.approx(b["ev"], abs=0.02)
    assert b["ev"] - b["debt"] + b["cash"] == pytest.approx(b["equity"], abs=0.02)
    assert b["equity"] / b["shares"] == pytest.approx(a["fair_value"], abs=0.01)
    assert b["pv_terminal"] / b["ev"] == pytest.approx(a["terminal_share"], abs=1e-3)
    assert [f["year"] for f in a["forecast"]] == [1, 2, 3, 4, 5]
    assert a["forecast"][0]["growth"] == pytest.approx(a["growth_y1"], abs=1e-4)
    assert a["terminal_growth"] == doc["rule"]["terminal_growth"]
    assert rows["BNK"]["bridge"] is None and rows["BNK"]["forecast"] is None


def test_only_options_tradable_names_are_valued(history, monkeypatch):
    """The fair-value tab is a screener: a liquid optionable name the liquidity verdict does not call
    tradable is never valued, only counted (2026-10-28)."""
    monkeypatch.setattr(
        valuation.securities, "liquid_optionable", lambda day=None: (["AAA", "LOSS", "NEW"], "public list")
    )
    out = valuation.build(SESSION, allowed={"AAA", "ZZZ"})
    assert out["ok"], out
    doc = json.loads(valuation.latest(SESSION).read_text(encoding="utf-8"))
    assert sorted(doc["rows"]) == ["AAA"]
    assert doc["not_tradable"] == 2 and doc["universe"] == "public list, options-tradable only"


def test_a_split_after_the_fiscal_year_scales_the_shares_and_is_flagged(history):
    # AAA's last statements end 2025-12-31 with 100 shares; a 25-for-1 on 2026-04-06 makes 2,500.
    # The raw closes drop 25-fold from the ex-date as a real split's do, so the value per share falls
    # with them and the gap to the price is unchanged. A 2-for-1 before the year's end is already in
    # the year's count and must not move it.
    before = valuation.build(SESSION, names=["AAA"])
    assert before["ok"]
    plain = json.loads(valuation.latest(SESSION).read_text(encoding="utf-8"))["rows"]["AAA"]
    conn = store.connect(paths.history_db())
    conn.execute(
        "UPDATE bars SET open = open / 25, high = high / 25, low = low / 25, close = close / 25"
        " WHERE symbol = 'AAA' AND date >= '2026-04-06'"
    )
    conn.execute(
        "UPDATE bars SET open = open * 2, high = high * 2, low = low * 2, close = close * 2"
        " WHERE symbol = 'AAA' AND date < '2025-06-02'"
    )
    store.upsert_splits(conn, [("AAA", "2026-04-06", 25.0, 1.0), ("AAA", "2025-06-02", 2.0, 1.0)])
    conn.commit()
    conn.close()
    valuation.build(SESSION, names=["AAA"])
    a = json.loads(valuation.latest(SESSION).read_text(encoding="utf-8"))["rows"]["AAA"]
    assert "split_adjusted" in a["flags"] and "split_adjusted" not in plain["flags"]
    assert a["market_cap"] == pytest.approx(a["spot"] * 2500.0, rel=1e-4)  # spot is rounded
    assert a["fair_value"] == pytest.approx(plain["fair_value"] / 25.0, rel=0.02)
    assert a["gap"] == pytest.approx(plain["gap"], abs=0.02)


def test_shares_after_splits_counts_only_splits_between_the_year_end_and_the_session():
    from cherrypick.technicals.adjust import Split

    splits = [Split("2025-06-02", 2.0, 1.0), Split("2026-04-06", 25.0, 1.0), Split("2026-11-02", 3.0, 1.0)]
    assert valuation.shares_after_splits(100.0, splits, "2025-12-31", "2026-10-27") == (2500.0, True)
    assert valuation.shares_after_splits(100.0, splits, "2026-06-30", "2026-10-27") == (100.0, False)
    reverse = [Split("2026-04-06", 1.0, 10.0)]  # a 1-for-10 reverse split
    assert valuation.shares_after_splits(100.0, reverse, "2025-12-31", "2026-10-27")[0] == 10.0


def test_a_valued_row_carries_the_reverse_dcf(history):
    valuation.build(SESSION, names=["AAA", "LOSS"])
    rows = json.loads(valuation.latest(SESSION).read_text(encoding="utf-8"))["rows"]
    a = rows["AAA"]
    assert a["best_margin"] == pytest.approx(0.10)  # every year in the fixture at a 10% margin
    assert a["best_growth"] == pytest.approx(0.05)
    assert (a["implied_margin"] > a["ebit_margin"]) == (a["spot"] > a["fair_value"])
    assert a["reach"] in ("below value", "within history", "beyond history")
    assert rows["LOSS"]["implied_margin"] is None and rows["LOSS"]["reach"] is None


def test_reach_reads_one_lever_at_a_time():
    from cherrypick.core.valuation import Implied

    def imp(margin, growth, edge=None):
        return Implied(margin, growth, edge, best_margin=0.20, best_growth=0.15)

    assert valuation.reach(90.0, 100.0, imp(0.10, 0.02)) == "below value"
    assert valuation.reach(150.0, 100.0, imp(0.18, 0.40)) == "within history"  # the margin fits
    assert valuation.reach(150.0, 100.0, imp(0.35, 0.12)) == "within history"  # the growth fits
    assert valuation.reach(150.0, 100.0, imp(0.35, 0.40)) == "beyond history"
    assert valuation.reach(150.0, 100.0, imp(None, 1.0, "above")) == "beyond history"


def test_the_risk_free_rate_is_the_treasury_ten_year_when_the_file_is_there(history):
    from cherrypick.core import treasury

    valuation.build(SESSION, names=["AAA"])
    doc = json.loads(valuation.latest(SESSION).read_text(encoding="utf-8"))
    assert doc["rule"]["risk_free"] == 0.04 and doc["rule"]["risk_free_source"].startswith("incomerules")
    p = treasury.path(2026)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('Date,"10 Yr"\n10/24/2026,4.25\n10/27/2026,4.40\n', encoding="utf-8")
    out = valuation.build(SESSION, names=["AAA"])
    assert out["ok"]
    doc = json.loads(valuation.latest(SESSION).read_text(encoding="utf-8"))
    # The last curve strictly before the session: the 24th, never the session's own.
    assert doc["rule"]["risk_free"] == pytest.approx(0.0425)
    assert doc["rule"]["risk_free_source"] == "treasury 10y 2026-10-24"
    assert doc["rows"]["AAA"]["wacc"] >= valuation._val.MIN_WACC


def test_a_valued_name_is_read_back_by_session(history):
    assert valuation.latest(SESSION) is None
    valuation.build(SESSION, names=["AAA"])
    assert valuation.load("2026-10-26") is None  # built for the 27th: not on or before the 26th
    assert valuation.load("2026-11-01")["rows"]["AAA"]["state"] == "valued"


def test_no_store_and_no_index_bars_are_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setenv("TECHNICALS_DATA_DIR", str(tmp_path))
    out = valuation.build(SESSION, names=["AAA"])
    assert out["ok"] is False and "no history store" in out["reason"]
    conn = sqlite3.connect(paths.history_db())
    conn.close()
    store.connect(paths.history_db()).close()
    monkeypatch.setattr(valuation.securities, "latest", lambda day=None: ([], {"day": None, "source": None}))
    monkeypatch.setattr(valuation.fundamentals, "sector_sources", lambda: {})
    out = valuation.build(SESSION, names=["AAA"])
    assert out["ok"] is False and "SPY" in out["reason"]


def test_weekly_log_returns_and_their_alignment():
    closes = [100.0, 101.0, 102.0, 103.0, 104.0, 110.0, 111.0, 112.0, 113.0, 114.0, 121.0]
    r = valuation.log_returns(closes)
    assert r == pytest.approx([math.log(1.10), math.log(1.21 / 1.10)])
    s = {f"2026-01-{d:02d}": float(d) for d in range(1, 29)}
    i = {f"2026-01-{d:02d}": 2.0 * d for d in range(2, 29)}  # misses the 1st
    rs, ri = valuation.aligned_returns(s, i, "2026-01-28")
    assert len(rs) == len(ri) == 5 and rs == pytest.approx(ri)
