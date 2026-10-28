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
