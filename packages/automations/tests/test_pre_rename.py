"""A home the v0.14.0 migration has not reached is refused, never run on as if it were new: a ledger
that still has `ensemble_` tables, and a config whose group is still called `ensemble`."""

from __future__ import annotations

import sqlite3

import pytest

from cherrypick.automations import config as config_mod
from cherrypick.automations import db


def test_a_ledger_with_the_old_table_names_is_refused_and_left_as_it_was(tmp_path):
    path = tmp_path / "paper_trades.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE ensemble_positions (position_id TEXT PRIMARY KEY)")
    con.execute("INSERT INTO ensemble_positions VALUES ('p1')")
    con.commit()
    con.close()
    with pytest.raises(db.PreRenameLedger, match="ensemble_positions"):
        db.connect(str(path))
    con = sqlite3.connect(path)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    con.close()
    assert names == {"ensemble_positions"}, "no new table was made beside the old one"


def test_a_new_ledger_opens(tmp_path):
    conn = db.connect(str(tmp_path / "fresh.db"))
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    assert "automations_positions" in names and not any(n.startswith("ensemble") for n in names)


@pytest.mark.parametrize("enabled", [False, True])
def test_a_config_still_naming_the_old_group_is_refused(enabled):
    """Only `groups.ensemble` on disk while the shipped eight are in `default`: read as written, `default`
    would be a group not named, so on, and a group switched off would start entering. It is refused."""
    with pytest.raises(ValueError, match="groups.ensemble"):
        config_mod.load_arms({"groups": {"ensemble": {"enabled": enabled}}})


def test_the_shipped_eight_are_in_the_default_group():
    arms = config_mod.load_arms({"groups": {"default": {"enabled": False}}})
    shipped = [a for a in arms.values() if a.get("group") is not None]
    assert shipped and all(a["group"] == "default" for a in shipped)
    assert not any(a["enabled"] for a in shipped)
