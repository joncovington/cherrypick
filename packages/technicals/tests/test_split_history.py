"""Splits from the public history (`scripts/fetch_split_history.py`) fill what Dolt's table misses.

On 2026-10-01 Dolt held three of TQQQ's eight splits, so its pre-2018 bars adjusted to five fake
crashes. The fetched rows are merged with Dolt's through `adjust.dedupe_splits`, so a split both
sources state applies once, and a row the raw prices contradicted is never applied.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

from cherrypick.technicals import paths, store

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_split_history.py"


def _bars(conn):
    """A 2-for-1 on 2024-06-03: 100 before, 50 from the ex-date on."""
    store.upsert_bars(
        conn,
        [
            ("AAA", "2024-05-30", 100, 101, 99, 100, 1000),
            ("AAA", "2024-05-31", 100, 101, 99, 100, 1000),
            ("AAA", "2024-06-03", 50, 51, 49, 50, 2000),
            ("AAA", "2024-06-04", 50, 51, 49, 50, 2000),
        ],
    )


def _public(rows):
    path = paths.split_history()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS split_history (symbol TEXT, ex_date TEXT, to_factor REAL, "
        "for_factor REAL, source TEXT, fetched_at TEXT, raw_ratio REAL, verified INTEGER)"
    )
    conn.executemany("INSERT INTO split_history VALUES (?, ?, ?, ?, 'test', 'now', NULL, ?)", rows)
    conn.commit()
    conn.close()


def _closes(conn):
    return [round(b.close, 6) for b in store.adjusted_bars(conn, "AAA")]


def test_without_the_public_table_dolt_alone_decides():
    conn = store.connect()
    _bars(conn)
    assert _closes(conn) == [100, 100, 50, 50]  # Dolt has no split: the jump stands


def test_a_split_dolt_misses_is_filled_from_the_public_history():
    conn = store.connect()
    _bars(conn)
    _public([("AAA", "2024-06-03", 2, 1, 1)])
    assert _closes(conn) == [50, 50, 50, 50]


def test_a_split_both_sources_state_is_applied_once():
    conn = store.connect()
    _bars(conn)
    store.upsert_splits(conn, [("AAA", "2024-06-03", 2, 1)])
    _public([("AAA", "2024-06-03", 2, 1, 1)])
    assert _closes(conn) == [50, 50, 50, 50]  # not 25: applied twice would halve the past again


def test_a_split_the_prices_contradicted_is_not_applied():
    conn = store.connect()
    _bars(conn)
    _public([("AAA", "2024-06-03", 2, 1, 0)])
    assert _closes(conn) == [100, 100, 50, 50]


def test_an_uncheckable_split_is_applied():
    """NULL: the clone could not check it (no bars there). The public record is the better guess."""
    conn = store.connect()
    _bars(conn)
    _public([("AAA", "2024-06-03", 2, 1, None)])
    assert _closes(conn) == [50, 50, 50, 50]


# --------------------------------------------------------------------------- the fetch script


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("fetch_split_history", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fetch_split_history"] = module
    spec.loader.exec_module(module)
    return module


# The page's own table shape (splithistory.com, seen 2026-10-01).
PAGE = (
    '<TR><TD align="center" style="padding: 4px">05/24/2018</TD>'
    '<TD align="center" style="padding: 4px">3 for 1</TD></TR>'
    '<TR><TD align="center" style="padding: 4px">02/25/2011</TD>'
    '<TD align="center" style="padding: 4px">2 for 1</TD></TR>'
    '<TR><TD align="center">06/01/2020</TD><TD align="center">1 for 10</TD></TR>'
)


def test_the_page_parses_oldest_first_with_ratios(script):
    assert script.parse(PAGE) == [
        ("2011-02-25", 2.0, 1.0),
        ("2018-05-24", 3.0, 1.0),
        ("2020-06-01", 1.0, 10.0),  # a reverse split: one share after for every ten before
    ]


def test_a_page_without_a_table_parses_to_nothing(script):
    assert script.parse("<html>rate limited</html>") == []


def test_the_price_check(script):
    assert script.verdict(1.963, 2, 1) == 1  # TQQQ 2011-02-25
    assert script.verdict(3.001, 3, 1) == 1  # TQQQ 2018-05-24
    assert script.verdict(1.0, 2, 1) == 0  # no jump on the day: contradicted
    assert script.verdict(None, 2, 1) is None  # nothing to check against
