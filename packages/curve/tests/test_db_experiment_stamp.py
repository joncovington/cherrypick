"""The advisor experiment id rides on an advised row (2026-09-16), beside its frozen params."""

from __future__ import annotations

import json

from cherrypick.core import advice as _core_advice

from cherrypick.curve import book as bookmod
from cherrypick.curve import db, paper_loop


def test_the_position_table_declares_and_migrates_experiment_id(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(curve_positions)")}
    assert "experiment_id" in cols
    assert "experiment_id" in db._ADDED_COLUMNS["curve_positions"]  # an older ledger gets it on connect


def test_the_stamp_is_the_shared_rule():
    assert _core_advice.stamp_for("advised:control", "exp-1") == "exp-1"
    assert _core_advice.stamp_for("control", "exp-1") is None


def _plan(symbol="VXX"):
    def leg(role, strike, action, mid):
        return {
            "leg_role": role,
            "occ_symbol": f"VXX   {role}{strike:g}",
            "streamer_symbol": f".{role}",
            "expiration": "2026-10-16",
            "strike": strike,
            "option_type": "call",
            "action": action,
            "bid": mid - 0.05,
            "ask": mid + 0.05,
            "mid": mid,
            "delta": 0.30,
        }

    return {
        "symbol": symbol,
        "expiration": "2026-10-16",
        "short_strike": 50.0,
        "long_strike": 55.0,
        "spot": 45.0,
        "short_mid": 2.0,
        "long_mid": 1.0,
        "credit": 1.0,
        "width": 5.0,
        "max_loss": 400.0,
        "credit_pct_of_width": 0.2,
        "dte": 30,
        "legs": [leg("short_call", 50.0, "Sell to Open", 2.0), leg("long_call", 55.0, "Buy to Open", 1.0)],
    }


TWO = {
    "day": "2026-09-17",
    "experiments": [
        {
            "experiment_id": "exp-a",
            "tag": "advised:take-35",
            "base": "control",
            "params": {"profit_take_pct": 0.35},
        },
        {
            "experiment_id": "exp-b",
            "tag": "advised:hook-take",
            "base": "hook",
            "params": {"profit_take_pct": 0.65},
        },
        {"experiment_id": "exp-c", "tag": "advised:rejected", "base": "control", "params": None},
    ],
}


def test_session_books_open_one_advised_book_per_experiment(monkeypatch):
    monkeypatch.setattr(paper_loop, "advice_decision", lambda cfg, day: TWO)
    books, advised = paper_loop.session_books({}, "2026-09-17")
    assert books == ["control", "noflip", "hook", "advised:take-35", "advised:hook-take"]
    assert advised["advised:hook-take"]["experiment_id"] == "exp-b"
    assert "advised:rejected" not in advised  # that experiment's baseline day, nobody else's


def test_session_books_read_a_legacy_decision_as_the_single_advised_base_book(monkeypatch):
    monkeypatch.setattr(
        paper_loop,
        "advice_decision",
        lambda cfg, day: {
            "day": day,
            "params": {"profit_take_pct": 0.35},
            "base_book": "control",
            "experiment_id": "exp-old",
        },
    )
    books, advised = paper_loop.session_books({}, "2026-09-17")
    assert books == ["control", "noflip", "hook", "advised:control"]
    assert advised["advised:control"]["experiment_id"] == "exp-old"


def test_session_books_are_the_base_roster_on_a_baseline_day(monkeypatch):
    monkeypatch.setattr(
        paper_loop, "advice_decision", lambda cfg, day: {"day": day, "params": None, "experiments": []}
    )
    assert paper_loop.session_books({}, "2026-09-17") == (["control", "noflip", "hook"], {})


def test_a_twin_of_hook_is_gated_as_a_hook_by_its_entry_not_its_tag():
    """The hook gate reads the base the decision entry names: `advised:hook-take` shadows hook."""
    from cherrypick.curve import engine

    assert engine.base_book("advised:hook-take", decision=TWO) == "hook"
    assert engine.base_book("advised:take-35", decision=TWO) == "control"


def test_each_advised_book_is_entered_with_its_own_overlay_and_stamp(tmp_path):
    """Two experiments, two books, two frozen overlays, two ids -- resolved per tag through the
    decision handed to `enter_position`; the base book stays unstamped."""
    conn = db.connect(str(tmp_path / "paper.db"))
    for tag in ("control", "advised:take-35", "advised:hook-take"):
        entry = next((e for e in TWO["experiments"] if e["tag"] == tag), None)
        bookmod.enter_position(
            conn,
            _plan(),
            {},
            tag,
            entry_session="2026-09-17",
            advice_params=entry and entry["params"],
            regime={"ratio": 0.9, "regime": "contango", "hook": False},
            experiment_id=TWO,
        )
    rows = {
        r["book"]: r for r in conn.execute("SELECT book, advice_params, experiment_id FROM curve_positions")
    }
    assert rows["control"]["experiment_id"] is None and rows["control"]["advice_params"] is None
    assert rows["advised:take-35"]["experiment_id"] == "exp-a"
    assert rows["advised:hook-take"]["experiment_id"] == "exp-b"
    assert json.loads(rows["advised:take-35"]["advice_params"]) == {"profit_take_pct": 0.35}
    assert json.loads(rows["advised:hook-take"]["advice_params"]) == {"profit_take_pct": 0.65}


def test_a_legacy_decision_stamps_the_legacy_book(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    legacy = {"day": "2026-09-17", "params": {"profit_take_pct": 0.35}, "experiment_id": "exp-old"}
    bookmod.enter_position(
        conn,
        _plan(),
        {},
        "advised:control",
        entry_session="2026-09-17",
        advice_params=legacy["params"],
        regime=None,
        experiment_id=legacy,
    )
    assert conn.execute("SELECT experiment_id FROM curve_positions").fetchone()["experiment_id"] == "exp-old"
