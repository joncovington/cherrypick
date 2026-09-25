"""earnings' read verbs never create or migrate the ledger.

The console runs get_excursions on a page load. Through _conn() -- DDL plus every migration on
each open -- that could migrate the live paper ledger, or create one in a home that had none (seen
2026-09-24). get_pnl_summary and get_excursions now open read-only.
"""

import argparse
import sqlite3

import pytest

from cherrypick.earnings import db_paper


@pytest.mark.parametrize("verb", [db_paper.cmd_get_excursions, db_paper.cmd_get_pnl_summary])
def test_a_read_verb_against_a_missing_ledger_creates_nothing(verb, tmp_path, monkeypatch):
    missing = tmp_path / "paper_trades.db"
    monkeypatch.setattr(db_paper, "DB_PATH", missing)
    with pytest.raises(sqlite3.OperationalError):
        verb(argparse.Namespace(strategy=None, profile=None))
    assert not missing.exists(), "a read verb created the ledger it was asked to read"
