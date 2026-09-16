"""The advisor experiment id rides on an advised row (2026-09-16), beside its frozen params."""

from __future__ import annotations

from cherrypick.core import advice as _core_advice

from cherrypick.pmcc import db


def test_the_position_table_declares_and_migrates_experiment_id(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pmcc_positions)")}
    assert "experiment_id" in cols
    assert "experiment_id" in db._ADDED_COLUMNS["pmcc_positions"]  # an older ledger gets it on connect


def test_the_stamp_is_the_shared_rule():
    assert _core_advice.stamp_for("advised:control", "exp-1") == "exp-1"
    assert _core_advice.stamp_for("control", "exp-1") is None
