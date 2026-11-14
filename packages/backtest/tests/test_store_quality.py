"""#1083: a chain store's quality report, on stores built from the fetches' own DDL with invented
rows; no vendor data."""

from __future__ import annotations

import sqlite3

import pytest

from cherrypick.backtest import chainfetch, intradayfetch
from cherrypick.backtest import storequality as sq


@pytest.fixture
def eod(tmp_path):
    p = tmp_path / "eod_chains.db"
    conn = sqlite3.connect(p)
    conn.executescript(chainfetch.SCHEMA)
    rows = []
    for session in ("2024-02-01", "2024-02-02"):
        for strike, bid, ask in ((95.0, 1.0, 1.1), (100.0, 2.0, 2.2), (105.0, 0.0, 0.1), (110.0, 0.5, 0.4)):
            for right in ("CALL", "PUT"):
                rows.append(("AAA", session, "2024-03-15", strike, right, bid, ask))
    conn.executemany(
        "INSERT INTO eod_chain_quotes (symbol, session, expiration, strike, right, bid, ask) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.executemany(
        "INSERT INTO eod_chain_requests VALUES (?, ?, ?, ?, 0, NULL, 0)",
        [("AAA", "2024-02-01", "2024-03-15", "complete"), ("AAA", "2024-02-05", "2024-03-15", "refused")],
    )
    conn.commit()
    conn.close()
    return p


def test_the_eod_report_counts_sessions_chains_strikes_and_quote_shapes(eod):
    doc = sq.report(eod, "eod_chains.db")
    y = doc["quotes"]["AAA"]["2024"]
    assert y["sessions"] == 2 and y["chains"] == 2 and y["rows"] == 16 and y["strikes_per_chain"] == 4.0
    # Of four strikes: two two-sided, one with no bid, one crossed.
    assert y["two_sided"] == 0.5 and y["zero_bid"] == 0.25 and y["crossed"] == 0.25
    assert doc["requests"]["eod_chain_requests"] == {"AAA": {"complete": 1, "refused": 1}}
    assert doc["requests"]["eod_chain_paths"] == {}


def test_the_intraday_report_counts_missing_greeks(tmp_path):
    p = tmp_path / "intraday_chains.db"
    conn = sqlite3.connect(p)
    conn.executescript(intradayfetch.SCHEMA)
    conn.executemany(
        "INSERT INTO intraday_chain_quotes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [("NDX", "2026-04-01", "2026-04-01", "10:00", 18000.0, "CALL", 5.0, 5.5, None, None, 18000.0),
         ("NDX", "2026-05-08", "2026-05-08", "10:00", 18000.0, "CALL", 5.0, 5.5, 0.5, 0.2, 18000.0)],
    )  # fmt: skip
    conn.commit()
    conn.close()
    y = sq.report(p, "intraday_chains.db")["quotes"]["NDX"]["2026"]
    assert y["delta_missing"] == 0.5 and y["iv_missing"] == 0.5


def test_a_declared_floor_names_every_year_that_falls_short(eod):
    doc = sq.report(eod, "eod_chains.db")
    assert sq.meets(doc, "AAA", ["2024"], min_two_sided=0.5, min_strikes=4) == []
    short = sq.meets(doc, "AAA", ["2024", "2025"], min_two_sided=0.6, min_strikes=5)
    assert short == [
        "AAA 2024: two-sided share under 0.6",
        "AAA 2024: under 5 strikes a chain",
        "AAA 2025: no quotes",
    ]
