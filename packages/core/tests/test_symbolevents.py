"""A release label's dates with the ranges they speak for (`core.events.release_dates`), and a symbol's
earnings and ex-dividend dates (`core.symbolevents`): covered only where a store speaks for them."""

from __future__ import annotations

import json
import sqlite3

from cherrypick.core import events as ev
from cherrypick.core import symbolevents as se


def store(tmp_path):
    cal = tmp_path / "calendar"
    cal.mkdir(parents=True)
    bea = {
        "Personal Income and Outlays": {
            "release_dates": ["2026-01-31T03:30:00+00:00", "2026-02-27T13:30:00+00:00"]
        },
        "Gross Domestic Product": {"release_dates": ["2025-12-23T13:30:00+00:00"]},
    }
    (cal / "bea.json").write_text(json.dumps(bea), encoding="utf-8")
    rows = [
        {"name": "Producer Price Index", "at": "2026-08-14"},
        {"name": "Employment Situation", "at": "2026-09-04"},
        {"name": "Consumer Price Index", "at": "2026-09-11"},
    ]
    doc = ev.merge_fred_history(None, rows, "2026-08-01", "2026-09-30")
    (cal / "fred_history.json").write_text(json.dumps(doc), encoding="utf-8")
    return cal


def test_a_bea_release_is_covered_for_each_year_the_file_holds(tmp_path):
    dates, cov = ev.release_dates("PCE", root=store(tmp_path))
    assert dates == ["2026-01-30", "2026-02-27"]  # the first is 22:30 ET the day before its UTC date
    assert cov == [["2025-01-01", "2026-12-31"]]


def test_a_fred_release_is_covered_where_fred_is(tmp_path):
    root = store(tmp_path)
    assert ev.release_dates("PPI", root=root) == (["2026-08-14"], [["2026-08-01", "2026-09-30"]])
    assert ev.release_dates("NFP", root=root)[0] == ["2026-09-04"]
    assert ev.release_dates("Nothing", root=root) == ([], [])
    assert ev.release_dates("FOMC", root=root) == ([], [])  # curated, not a store's


def test_symbol_dates_are_covered_by_the_store_that_holds_them():
    doc = se.build(
        ["spy", "XYZ"],
        earnings=[("SPY", "2026-01-20"), ("ABC", "2026-01-21")],
        earnings_span=("2025-06-01", "2027-01-31"),
        dividends=[("SPY", "2026-03-20"), ("SPY", "2026-06-19")],
        bar_spans={"SPY": ("2011-01-03", "2026-11-13")},
        generated_at="t",
    )
    lab = doc["labels"]
    assert lab["Earnings"]["SPY"] == {"dates": ["2026-01-20"], "covered": [["2025-06-01", "2027-01-31"]]}
    assert lab["Earnings"]["XYZ"]["dates"] == []  # the calendar lists none for it in its span
    assert "ABC" not in lab["Earnings"]  # only the symbols asked for
    assert lab["ExDividend"]["SPY"]["dates"] == ["2026-03-20", "2026-06-19"]
    assert "XYZ" not in lab["ExDividend"]  # no bars in the store: unknown, never "no dividend"
    nocal = se.build(["SPY"], earnings=[], earnings_span=None, dividends=[], bar_spans={})
    assert nocal["labels"] == {"Earnings": {}, "ExDividend": {}}


class FakeDolt:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return self

    def execute(self, sql, args=None):
        self.last = sql

    def fetchone(self):
        return (min(d for _, d in self.rows), max(d for _, d in self.rows))

    def fetchall(self):
        return self.rows

    def close(self):
        pass


def test_write_reads_both_stores_and_read_gives_them_back(tmp_path, monkeypatch):
    tech = tmp_path / "technicals"
    tech.mkdir()
    conn = sqlite3.connect(tech / "history.db")
    conn.executescript(
        "CREATE TABLE bars (symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL);"
        "CREATE TABLE dividends (symbol TEXT, ex_date TEXT, amount REAL);"
        "INSERT INTO bars VALUES ('SPY', '2020-01-02', 1, 1, 1, 1, 1), ('SPY', '2026-11-13', 1, 1, 1, 1, 1),"
        " ('GONE', '2019-01-02', 1, 1, 1, 1, 1), ('GONE', '2026-10-01', 1, 1, 1, 1, 1);"
        "INSERT INTO dividends VALUES ('SPY', '2026-09-18', 1.8);"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(se, "_technicals_db", lambda: tech / "history.db")
    assert se.stored_symbols() == ["SPY"]
    out = tmp_path / "symbol_events.json"
    se.write(["SPY"], connect=lambda db: FakeDolt([("SPY", "2026-10-28")]), out=out)
    got = se.read(out)
    assert got["ExDividend"]["SPY"] == (["2026-09-18"], [["2020-01-02", "2026-11-13"]])
    assert got["Earnings"]["SPY"] == (["2026-10-28"], [["2026-10-28", "2026-10-28"]])
    se.write(["SPY"], connect=None, out=out)
    assert "SPY" not in se.read(out)["Earnings"]  # without Dolt, earnings are not covered
    assert se.read(tmp_path / "missing.json") == {}
