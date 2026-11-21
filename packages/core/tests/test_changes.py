"""`core.changes`: every modification under test in one module, with a declared reading."""

from __future__ import annotations

import json
import sqlite3

import pytest

from cherrypick.core import changes


def _block(sessions, mean, ci, excludes, to_zero):
    return {
        "sessions": sessions,
        "mean_diff": mean,
        "ci": ci,
        "ci_level": 0.95,
        "ci_excludes_zero": excludes,
        "sessions_to_exclude_zero": to_zero,
        "first": "2026-10-01",
        "last": "2026-11-20",
    }


def _vs(sessions, mean, ci, excludes, to_zero, per_entry=None):
    return {**_block(sessions, mean, ci, excludes, to_zero), "per_entry": per_entry}


def _arm(name, vs, *, advised=False, exp=None, status=None, control="control"):
    return {
        "arm": name,
        "advised": advised,
        "experiment_id": exp,
        "experiment_status": status,
        "control": control,
        "vs_control": vs,
    }


@pytest.mark.parametrize(
    "block, closed, want",
    [
        (_block(30, 12.0, [2.0, 22.0], True, 30), False, ("ready", "better", 0)),
        (_block(30, -5.0, [-12.0, 2.0], False, 50), False, ("leaning", "worse", 20)),
        (_block(30, 1.0, [-9.0, 11.0], False, 900), False, ("too_early", "better", 870)),
        (_block(30, 0.0, [-9.0, 9.0], False, None), False, ("too_early", "even", None)),
        # Clear of zero over five sessions is still too early; the minimum is still to come.
        (_block(5, 40.0, [10.0, 70.0], True, 3), False, ("too_early", "better", 5)),
        (_block(30, 12.0, [2.0, 22.0], True, 30), True, ("closed", "better", 0)),
        (None, False, ("too_early", None, None)),
    ],
)
def test_the_reading_is_declared_once(block, closed, want):
    r = changes.reading(block if block is None else {**block, "per_entry": None}, closed=closed)
    assert (r["reading"], r["direction"], r["sessions_to_decision"]) == want


def test_the_reading_is_on_the_per_trade_pairing_where_there_is_one():
    # Per session the variant looks better only because it enters more; per trade it is worse.
    vs = _vs(30, 400.0, [100.0, 700.0], True, 20, per_entry=_block(30, -3.0, [-6.0, -0.5], True, 25))
    r = changes.reading(vs)
    assert (r["basis"], r["reading"], r["direction"]) == ("per_trade", "ready", "worse")
    assert changes.reading(_vs(30, 4.0, [1.0, 7.0], True, 20))["basis"] == "per_session"


def test_rows_name_an_advised_twin_by_its_experiment_and_close_an_expired_one():
    comparison = {
        "arms": [
            {"arm": "control", "control": None, "vs_control": None},
            _arm("bp-10k", _vs(30, 1.0, [-9.0, 11.0], False, 900)),
            _arm(
                "advised:pead-gate-off:iron_fly",
                _vs(30, 3.0, [1.0, 5.0], True, 10),
                advised=True,
                exp="exp-1",
                control="strat_test:iron_fly",
            ),
            _arm(
                "advised:late-window",
                _vs(30, 3.0, [1.0, 5.0], True, 10),
                advised=True,
                exp="exp-2",
                status="expired",
            ),
        ]
    }
    details = {
        "exp-1": {
            "name": "pead-gate-off",
            "hypothesis": "Closing a loser early beats carrying it. A replay of 35 says so.",
            "status": "active",
        },
        "exp-2": {"name": "late-window", "hypothesis": None, "status": "expired"},
    }
    out = changes.rows(comparison, details)
    assert [r["name"] for r in out] == ["pead-gate-off (iron_fly)", "bp-10k", "late-window"]
    first = out[0]
    assert (first["kind"], first["id"], first["against"], first["reading"]) == (
        "advised",
        "exp-1",
        "strat_test:iron_fly",
        "ready",
    )
    assert first["description"] == "Closing a loser early beats carrying it."
    assert out[-1]["reading"] == "closed"
    # Control is never a change of itself.
    assert all(r["arm"] != "control" for r in out)


def test_a_declared_item_waits_on_its_held_out_test_and_closes_with_its_verdict():
    declares = [
        {
            "source_kind": "idea",
            "automation": "c1",
            "result": "ideas-c1-2611201000",
            "item": "vix_high",
            "title": "c1: vix high",
            "measure": "m",
            "licensed": True,
            "held_out": None,
        },
        {
            "source_kind": "setting",
            "automation": "c1",
            "result": "grid-c1-2611201000",
            "item": "stop_2x",
            "title": "c1: stop 2x",
            "licensed": False,
            "held_out": {"verdict": "failed", "ci": [-3, 4]},
        },
    ]
    out = changes.rows({"arms": []}, {}, declares)
    assert [(r["name"], r["reading"], r["status"]) for r in out] == [
        ("c1: vix high", "waiting", "declared"),
        ("c1: stop 2x", "closed", "tested"),
    ]
    assert out[0]["source"] == {"kind": "idea", "result": "ideas-c1-2611201000", "licensed": True}
    assert out[1]["held_out"]["verdict"] == "failed"


def test_open_changes_come_first():
    comparison = {
        "arms": [
            _arm("z-too-early", _vs(30, 1.0, [-9.0, 11.0], False, 900)),
            _arm("a-ready", _vs(30, 3.0, [1.0, 5.0], True, 10)),
            _arm("m-leaning", _vs(30, -5.0, [-12.0, 2.0], False, 50)),
        ]
    }
    assert [r["reading"] for r in changes.rows(comparison)] == ["ready", "leaning", "too_early"]


def test_the_advisor_store_is_read_for_names_and_hypotheses(tmp_path):
    db = tmp_path / "advisor.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE experiments (id TEXT PRIMARY KEY, module TEXT, base_profile TEXT, name TEXT, "
        "hypothesis TEXT, status TEXT)"
    )
    conn.execute(
        "INSERT INTO experiments VALUES ('exp-1', 'meic', 'control', 'late', 'Later is better.', 'active')"
    )
    conn.commit()
    conn.close()
    got = changes.experiments(db)
    assert got["exp-1"]["name"] == "late" and got["exp-1"]["sessions_run"] is None
    assert changes.experiments(tmp_path / "none.db") == {}


def test_declares_are_read_from_the_backtests_reply():
    assert changes.load_declares(json.dumps({"ok": True, "items": [{"item": "x"}]})) == [{"item": "x"}]
    assert changes.load_declares(None) == []
    with pytest.raises(ValueError):
        changes.load_declares(json.dumps({"items": "no"}))


def test_first_sentence():
    assert changes.first_sentence("One. Two.") == "One."
    assert changes.first_sentence(None) is None
    assert changes.first_sentence("x" * 200).endswith("…")
