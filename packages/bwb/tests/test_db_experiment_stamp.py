"""The advisor experiment id rides on an advised row (2026-09-16), beside its frozen params."""

from __future__ import annotations

import json

from cherrypick.core import advice as _core_advice

from cherrypick.bwb import book as bookmod
from cherrypick.bwb import db, paper_loop


def test_the_position_table_declares_and_migrates_experiment_id(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(bwb_positions)")}
    assert "experiment_id" in cols
    assert "experiment_id" in db._ADDED_COLUMNS["bwb_positions"]  # an older ledger gets it on connect


def test_the_stamp_is_the_shared_rule():
    assert _core_advice.stamp_for("advised:control", "exp-1") == "exp-1"
    assert _core_advice.stamp_for("control", "exp-1") is None


def _plan(symbol="SPX"):
    def leg(role, strike, action, mid):
        return {
            "leg_role": role,
            "occ_symbol": f"SPXW  {role}{strike:g}",
            "streamer_symbol": f".{role}",
            "expiration": "2026-09-24",
            "strike": strike,
            "option_type": "put",
            "action": action,
            "bid": mid - 0.1,
            "ask": mid + 0.1,
            "mid": mid,
        }

    return {
        "symbol": symbol,
        "expiration": "2026-09-24",
        "body_strike": 6400.0,
        "near_strike": 6380.0,
        "far_strike": 6370.0,
        "spot": 6500.0,
        "body_mid": 10.0,
        "near_mid": 6.0,
        "far_mid": 5.0,
        "credit": 1.0,
        "narrow_width": 20.0,
        "wide_width": 30.0,
        "max_loss": 900.0,
        "dte": 7,
        "legs": [leg("body", 6400.0, "Sell to Open", 10.0), leg("near", 6380.0, "Buy to Open", 6.0)],
    }


TWO = {
    "day": "2026-09-17",
    "experiments": [
        {
            "experiment_id": "exp-a",
            "tag": "advised:early-delta",
            "base": "delta",
            "params": {"delta_trigger": 0.35},
        },
        {
            "experiment_id": "exp-b",
            "tag": "advised:late-delta",
            "base": "delta",
            "params": {"delta_trigger": 0.65},
        },
        {"experiment_id": "exp-c", "tag": "advised:rejected", "base": "delta", "params": None},
    ],
}


def test_session_books_open_one_advised_book_per_experiment(monkeypatch):
    monkeypatch.setattr(paper_loop, "advice_decision", lambda cfg, day: TWO)
    books, advised = paper_loop.session_books({}, "2026-09-17")
    assert books == ["control", "delta", "bounce", "flip", "advised:early-delta", "advised:late-delta"]
    assert advised["advised:late-delta"]["experiment_id"] == "exp-b"
    assert "advised:rejected" not in advised  # that experiment's baseline day, nobody else's


def test_session_books_read_a_legacy_decision_as_the_single_advised_base_book(monkeypatch):
    monkeypatch.setattr(
        paper_loop,
        "advice_decision",
        lambda cfg, day: {
            "day": day,
            "params": {"delta_trigger": 0.35},
            "base_book": "delta",
            "experiment_id": "exp-old",
        },
    )
    books, advised = paper_loop.session_books({}, "2026-09-17")
    assert books == ["control", "delta", "bounce", "flip", "advised:delta"]
    assert advised["advised:delta"]["experiment_id"] == "exp-old"


def test_session_books_are_the_base_roster_on_a_baseline_day(monkeypatch):
    monkeypatch.setattr(
        paper_loop, "advice_decision", lambda cfg, day: {"day": day, "params": None, "experiments": []}
    )
    assert paper_loop.session_books({}, "2026-09-17") == (["control", "delta", "bounce", "flip"], {})


def test_each_advised_book_is_entered_with_its_own_overlay_and_stamp(tmp_path):
    """Two experiments, two books, two frozen overlays, two ids -- resolved per tag through the
    decision handed to `enter_position`; the base book stays unstamped."""
    conn = db.connect(str(tmp_path / "paper.db"))
    for tag in ("delta", "advised:early-delta", "advised:late-delta"):
        entry = next((e for e in TWO["experiments"] if e["tag"] == tag), None)
        bookmod.enter_position(
            conn,
            _plan(),
            {},
            tag,
            entry_session="2026-09-17",
            advice_params=entry and entry["params"],
            experiment_id=TWO,
        )
    rows = {
        r["book"]: r
        for r in conn.execute("SELECT book, advice_params, experiment_id, advice_base FROM bwb_positions")
    }
    assert rows["delta"]["experiment_id"] is None and rows["delta"]["advice_params"] is None
    # the base each twin shadows is stamped from the decision entry; a base book carries none
    assert rows["delta"]["advice_base"] is None
    assert rows["advised:early-delta"]["advice_base"] == "delta"
    assert rows["advised:late-delta"]["advice_base"] == "delta"
    assert rows["advised:early-delta"]["experiment_id"] == "exp-a"
    assert rows["advised:late-delta"]["experiment_id"] == "exp-b"
    assert json.loads(rows["advised:early-delta"]["advice_params"]) == {"delta_trigger": 0.35}
    assert json.loads(rows["advised:late-delta"]["advice_params"]) == {"delta_trigger": 0.65}


def test_a_legacy_decision_stamps_the_legacy_book(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    legacy = {"day": "2026-09-17", "params": {"delta_trigger": 0.35}, "experiment_id": "exp-old"}
    bookmod.enter_position(
        conn,
        _plan(),
        {},
        "advised:control",
        entry_session="2026-09-17",
        advice_params=legacy["params"],
        experiment_id=legacy,
    )
    row = conn.execute("SELECT experiment_id FROM bwb_positions").fetchone()
    assert row["experiment_id"] == "exp-old"
