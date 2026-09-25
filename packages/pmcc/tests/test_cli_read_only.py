"""The read CLI never creates or migrates a ledger.

Its verbs are all reads, and the console shells out to them on a page load (excursionsBridge,
calendarsBridge). They used to open through the write path, db.connect, which applies the schema
and migrations -- so a read could migrate the live ledger, and against a home with no ledger yet it
created one (seen 2026-09-24). A read of a missing ledger must fail, not conjure an empty one.
"""

import sqlite3

import pytest

from cherrypick.pmcc import cli


def test_a_read_verb_against_a_missing_ledger_creates_nothing(tmp_path):
    missing = tmp_path / "paper_trades.db"
    with pytest.raises(sqlite3.OperationalError):
        cli.main(["--db", str(missing), "excursions"])
    assert not missing.exists(), "a read verb created the ledger it was asked to read"
