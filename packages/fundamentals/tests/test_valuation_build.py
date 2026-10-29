"""The fair-value build (valuation.py, version 2): one valued stock, a fund, a bank and refused names,
built from a temp technicals store (the bars) and a temp fundamentals store (statements, consensus,
the SEC filer rows) with Damodaran's files beside it, and the file it writes."""

from __future__ import annotations

import json
import math
from datetime import date, timedelta

import pytest
from cherrypick.technicals import paths as tpaths
from cherrypick.technicals import store

from cherrypick.fundamentals import consensus, paths, secfacts, statements, valuation

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
            (
                symbol,
                end,
                rev,
                rev * ebit_margin,
                rev * 0.05,
                rev * 0.09,
                rev * 0.09 * 0.25,
                10.0,
                rev * 0.06,
                rev * 0.14,
                rev * 0.05,
                -rev * 0.01,
                200.0,
                0.0,
                100.0,
                100.0,
                500.0,
            )
        )
    conn.executemany(
        f"INSERT OR REPLACE INTO statements_annual VALUES ({', '.join('?' for _ in statements.COLUMNS)})",
        rows,
    )


RATINGS = {
    "as_of": "January 2026",
    "non_financial": [[-100000.0, 1.249999, "Caa/CCC", 0.0885], [1.25, 100000.0, "Aaa/AAA", 0.004]],
    "financial": [],
}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("TECHNICALS_DATA_DIR", str(tmp_path / "t"))
    monkeypatch.setenv("FUNDAMENTALS_DATA_DIR", str(tmp_path / "f"))
    bars = store.connect(tpaths.history_db())
    _bars(bars, "SPY", 400.0, 0.0003, 0.01)
    _bars(bars, "AAA", 50.0, 0.0004, 0.02)  # moves with the index, about twice as much
    for s in ("BNK", "FND", "LOSS", "NEW", "FOR", "OLD"):
        _bars(bars, s, 30.0, 0.0003, 0.01)
    bars.commit()
    bars.close()
    facts = secfacts.connect()
    facts.executescript(statements.SCHEMA + consensus.SCHEMA)
    for s in ("AAA", "BNK", "FOR", "OLD"):
        _statements(facts, s)
    _statements(facts, "LOSS", ebit_margin=-0.05)
    # Year one is the current fiscal year (2026), year two the next: ten analysts each.
    facts.executemany(
        "INSERT INTO sales_consensus VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("AAA", "2026-10-25", "Current Year", "2026-12-31", 1300.0, 10, 1350.0, 1250.0, 1215.5),
            ("AAA", "2026-10-25", "Next Year", "2027-12-31", 1400.0, 10, 1500.0, 1300.0, 1300.0),
        ],
    )
    facts.commit()
    facts.close()
    dam = paths.damodaran_dir()
    dam.mkdir(parents=True)
    (dam / "ratings.json").write_text(json.dumps(RATINGS), encoding="utf-8")
    (dam / "erp.json").write_text(json.dumps({"months": {"2026-10-01": 0.042}}), encoding="utf-8")
    # Stand in for the security list, the sector maps and the broker file: local files only.
    monkeypatch.setattr(
        valuation.securities,
        "latest",
        lambda day=None: (
            [
                {"symbol": s, "type": "Exchange Traded Fund" if s == "FND" else "Stock"}
                for s in ("AAA", "BNK", "FND", "LOSS", "NEW", "FOR", "OLD")
            ],
            {"day": SESSION, "source": "public"},
        ),
    )
    monkeypatch.setattr(valuation._score, "sector_sources", lambda: {"BNK": ("Financials", "yahoo")})
    monkeypatch.setattr(
        valuation, "broker_readings", lambda as_of: {"AAA": {"beta": 0.5, "market_cap": 5.0e3}}
    )
    return tmp_path


def _built(names) -> dict:
    out = valuation.build(SESSION, names=names)
    assert out["ok"], out
    return json.loads(valuation.latest(SESSION).read_text(encoding="utf-8"))


def test_the_build_values_a_stock_and_says_why_the_others_have_no_value(world):
    doc = _built(["AAA", "BNK", "FND", "LOSS", "NEW"])
    rows = doc["rows"]
    assert doc["version"] == valuation.VERSION == 2 and doc["session"] == SESSION
    assert (rows["FND"]["state"], rows["FND"]["reason"]) == ("not-applicable", "fund")
    assert (rows["BNK"]["state"], rows["BNK"]["reason"]) == (
        "not-applicable",
        "financials",
    )  # no SIC: the sector map
    assert (rows["LOSS"]["state"], rows["LOSS"]["reason"]) == ("refused", "no_operating_profit")
    assert rows["NEW"]["state"] == "no-statements"
    a = rows["AAA"]
    assert a["state"] == "valued" and a["low"] <= a["fair_value"] <= a["high"]
    assert a["gap"] == pytest.approx(a["spot"] / a["fair_value"] - 1, rel=1e-2)
    # Year one is the current year's consensus against the last reported year (FY2025: 1,215.51).
    assert a["consensus_used"] and a["growth_y1"] == pytest.approx(1300.0 / (1000 * 1.05**4) - 1, abs=1e-4)
    assert [f["growth_source"] for f in a["forecast"][:3]] == ["consensus", "consensus", "fade"]
    assert len(a["forecast"]) == 10
    c = a["capital"]
    assert c["erp"] == 0.042 and c["erp_month"] == "2026-10-01" and c["rating"] == "Aaa/AAA"
    assert c["coverage"] == pytest.approx(a["history"][-1]["ebit_margin"] * 1000 * 1.05**4 / 10.0, rel=1e-3)
    assert c["beta"] == pytest.approx(0.67 * c["beta_raw"] + 0.33, abs=1e-3)
    assert "beta_disagrees" in a["flags"] and "shares_stale" in a["flags"]
    # The bridge is the base case's own arithmetic: it adds up to the value.
    b = a["bridge"]
    assert b["pv_forecast"] + b["pv_terminal"] == pytest.approx(b["ev"], abs=0.02)
    assert b["equity"] / b["shares"] == pytest.approx(a["fair_value"], abs=0.01)
    assert a["terminal"]["reinvestment_rate"] == pytest.approx(
        valuation._val.TERMINAL_GROWTH / a["wacc"], rel=1e-3
    )
    assert a["sources"]["revenue"] == "Dolt" and a["data"]["source"] == "Dolt statements"
    assert rows["BNK"]["bridge"] is None and rows["BNK"]["forecast"] is None


def _filer(
    sym: str, sic: str, foreign: int = 0, last_period: str = "2026-09-30", last_filed: str = "2026-10-20"
):
    conn = secfacts.connect()
    conn.execute(
        "INSERT OR REPLACE INTO sec_filers VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (sym, 1, sym, sic, "x", "1231", foreign, "10-Q", last_period, last_filed, "t"),
    )
    conn.commit()
    conn.close()


def test_the_filing_decides_what_is_an_operating_business(world):
    """A SIC code decides over the sector map (a payments company the map calls Financials is valued);
    a foreign filer is refused; a bank's SIC refuses whatever the map says."""
    _filer("AAA", "7389")  # services: an operating business
    _filer("BNK", "7389")  # the sector map says Financials; the filing says otherwise
    _filer("FOR", "3674", foreign=1)
    _filer("OLD", "6021")  # a national bank
    rows = _built(["AAA", "BNK", "FOR", "OLD"])["rows"]
    assert rows["BNK"]["state"] == "valued"
    assert (rows["FOR"]["state"], rows["FOR"]["reason"]) == ("not-applicable", "foreign_filer")
    assert (rows["OLD"]["state"], rows["OLD"]["reason"]) == ("not-applicable", "a bank")


def test_results_too_old_to_value_today_are_refused(world):
    _filer("OLD", "7389", last_period="2026-03-31", last_filed="2026-04-30")
    conn = secfacts.connect()
    # One filed fact, so the year reads as the filings' (dated by its quarters, not by Dolt's year).
    conn.execute(
        "INSERT INTO sec_facts VALUES "
        "('OLD','Revenues','USD','2025-01-01','2025-12-31',1215.5,'10-K','FY','2026-02-10','a')"
    )
    conn.commit()
    conn.close()
    assert _built(["OLD"])["rows"]["OLD"]["reason"] == "stale_results"


def test_a_manufacturer_with_a_lending_arm_is_refused(world):
    _filer("AAA", "3711")
    conn = secfacts.connect()
    conn.executemany(
        "INSERT INTO sec_facts VALUES (?,?,'USD',?,?,?,'10-K','FY','2026-02-10','a')",
        [
            ("AAA", "Revenues", "2025-01-01", "2025-12-31", 1215.5),
            ("AAA", "PaymentsToAcquireFinanceReceivables", "2025-01-01", "2025-12-31", 300.0),
        ],
    )
    conn.commit()
    conn.close()
    assert _built(["AAA"])["rows"]["AAA"]["reason"] == "captive_finance"
    _filer("AAA", "7372")  # software originating the same loans is not a manufacturer: valued
    assert _built(["AAA"])["rows"]["AAA"]["state"] == "valued"


def test_without_damodaran_s_files_the_erp_falls_back_and_the_rating_refuses(world):
    (paths.damodaran_dir() / "erp.json").unlink()
    a = _built(["AAA"])["rows"]["AAA"]
    assert a["capital"]["erp"] == valuation.FALLBACK_ERP and "erp_fallback" in a["flags"]
    (paths.damodaran_dir() / "ratings.json").unlink()
    assert _built(["AAA"])["rows"]["AAA"]["reason"] == "no_rating_table"


def test_only_options_tradable_names_are_valued(world, monkeypatch):
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


def test_a_split_after_the_fiscal_year_scales_the_shares_and_is_flagged(world):
    # AAA's last statements end 2025-12-31 with 100 shares; a 25-for-1 on 2026-04-06 makes 2,500.
    # The raw closes drop 25-fold from the ex-date as a real split's do, so the value per share falls
    # with them and the gap to the price is unchanged. A 2-for-1 before the year's end is already in
    # the year's count and must not move it.
    plain = _built(["AAA"])["rows"]["AAA"]
    conn = store.connect(tpaths.history_db())
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
    a = _built(["AAA"])["rows"]["AAA"]
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


def test_a_valued_row_carries_the_reverse_dcf(world):
    rows = _built(["AAA", "LOSS"])["rows"]
    a = rows["AAA"]
    assert a["best_margin"] == pytest.approx(0.10)  # every year in the fixture at a 10% margin
    assert a["best_growth"] == pytest.approx(0.05)
    assert (a["implied_margin"] > a["ebit_margin"]) == (a["spot"] > a["fair_value"])
    assert a["implied_wacc"] is None or (a["implied_wacc"] < a["wacc"]) == (a["spot"] > a["fair_value"])
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


def test_the_risk_free_rate_is_the_treasury_ten_year_when_the_file_is_there(world):
    from cherrypick.core import treasury

    doc = _built(["AAA"])
    assert doc["rule"]["risk_free"] == 0.04 and doc["rule"]["risk_free_source"].startswith("incomerules")
    p = treasury.path(2026)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('Date,"10 Yr"\n10/24/2026,4.25\n10/27/2026,4.40\n', encoding="utf-8")
    doc = _built(["AAA"])
    # The last curve strictly before the session: the 24th, never the session's own.
    assert doc["rule"]["risk_free"] == pytest.approx(0.0425)
    assert doc["rule"]["risk_free_source"] == "10-year Treasury 2026-10-24"
    assert doc["rows"]["AAA"]["capital"]["rf"] == pytest.approx(0.0425)


def test_a_valued_name_is_read_back_by_session(world):
    assert valuation.latest(SESSION) is None
    _built(["AAA"])
    assert valuation.load("2026-10-26") is None  # built for the 27th: not on or before the 26th
    assert valuation.load("2026-11-01")["rows"]["AAA"]["state"] == "valued"


def test_no_store_and_no_index_bars_are_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setenv("TECHNICALS_DATA_DIR", str(tmp_path / "t"))
    monkeypatch.setenv("FUNDAMENTALS_DATA_DIR", str(tmp_path / "f"))
    out = valuation.build(SESSION, names=["AAA"])
    assert out["ok"] is False and "no technicals history store" in out["reason"]
    store.connect(tpaths.history_db()).close()
    monkeypatch.setattr(valuation.securities, "latest", lambda day=None: ([], {"day": None, "source": None}))
    monkeypatch.setattr(valuation._score, "sector_sources", lambda: {})
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
