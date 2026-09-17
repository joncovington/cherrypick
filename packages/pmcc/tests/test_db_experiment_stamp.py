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


def test_each_advised_book_is_stamped_with_its_own_experiment_from_the_decision():
    """Two experiments on one session are two books with two ids (2026-09-17) -- the stamp is
    resolved per tag through the decision, never one id shared across the twins."""
    decision = {
        "day": "2026-09-17",
        "experiments": [
            {
                "experiment_id": "exp-a",
                "tag": "advised:tv-exit",
                "base": "control",
                "params": {"tv_managed_exit": True},
            },
            {
                "experiment_id": "exp-b",
                "tag": "advised:tv-05",
                "base": "control",
                "params": {"tv_close_threshold": 0.05},
            },
        ],
    }
    assert _core_advice.stamp_for("advised:tv-exit", decision) == "exp-a"
    assert _core_advice.stamp_for("advised:tv-05", decision) == "exp-b"
    assert _core_advice.stamp_for("control", decision) is None
    legacy = {"day": "2026-09-17", "params": {"tv_managed_exit": True}, "experiment_id": "exp-old"}
    assert _core_advice.stamp_for("advised:control", legacy) == "exp-old"
