"""The strat_test per-strategy portfolio mechanism: how a forced-sampling trade is tagged to a book
(strat_test_portfolio), which tags run_closes sweeps, and the book-family filter that reporting uses to
match a tag against its per-strategy sub-books without the LIKE-wildcard over-match those tags invite.
"""

import sqlite3

from cherrypick.earnings import strategy_metrics as sm
from cherrypick.earnings import strategy_test_runner as r


def test_book_tag_defaults_to_per_strategy():
    # Missing strat_test_portfolio -> per_strategy (each strategy its own book).
    assert r._book_tag({}, "iron_fly") == "strat_test:iron_fly"
    assert r._book_tag({"strat_test_portfolio": "per_strategy"}, "atm_calendar") == "strat_test:atm_calendar"


def test_book_tag_combined_keeps_single_book():
    assert r._book_tag({"strat_test_portfolio": "combined"}, "iron_fly") == "strat_test"
    assert r._book_tag({"strat_test_portfolio": "combined"}, "double_calendar") == "strat_test"


def test_is_strat_test_book_matches_combined_and_per_strategy_only():
    assert r._is_strat_test_book("strat_test") is True
    assert r._is_strat_test_book("strat_test:iron_fly") is True
    assert r._is_strat_test_book("default") is False
    assert r._is_strat_test_book("") is False
    assert r._is_strat_test_book(None) is False


def test_book_family_filter_matches_book_and_subbooks_but_not_lookalikes():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (profile TEXT)")
    conn.executemany(
        "INSERT INTO t VALUES (?)",
        [
            ("strat_test",),
            ("strat_test:iron_fly",),
            ("strat_test:atm_calendar",),
            ("default",),
            ("stratXtest:iron_fly",),  # the LIKE '_' wildcard trap: must NOT match
        ],
    )
    frag, params = sm.book_family_filter("strat_test")
    rows = {row[0] for row in conn.execute(f"SELECT profile FROM t WHERE {frag}", params)}
    conn.close()
    assert rows == {"strat_test", "strat_test:iron_fly", "strat_test:atm_calendar"}
