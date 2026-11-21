"""`read declared`: every declared idea and setting with its one held-out test or none, from synthetic
results (no vendor data)."""

from __future__ import annotations

from cherrypick.backtest import declared


def _idea_doc():
    return {
        "name": "ideas-c1-2611201000",
        "automation": "c1",
        "run": "builder-c1-2611150900",
        "declared": [
            {
                "idea": "vix_high",
                "at": "2026-11-20T10:00:00-05:00",
                "row": {"title": "c1: vix high", "measure": "the net of the skipped sessions"},
            },
            {"idea": "gap_down", "at": "2026-11-19T10:00:00-05:00", "row": {"title": "c1: gap down"}},
        ],
        "held_out_uses": [
            {
                "idea": "gap_down",
                "at": "2026-11-20T11:00:00-05:00",
                "use": 1,
                "verdict": "failed",
                "ci": [-5.0, 12.0],
            }
        ],
    }


def _grid_doc():
    return {
        "name": "grid-c2-2611201000",
        "automation": "c2",
        "declared": [
            {"setting": "stop_2x", "at": "2026-11-18T10:00:00-05:00", "row": {"title": "c2: stop 2x"}}
        ],
    }


def test_each_declared_item_carries_its_title_and_its_held_out_test_or_none():
    out = declared.items([("idea", "idea", _idea_doc(), True), ("setting", "setting", _grid_doc(), False)])
    assert [(r["item"], r["source_kind"], r["licensed"]) for r in out] == [
        ("vix_high", "idea", True),
        ("gap_down", "idea", True),
        ("stop_2x", "setting", False),
    ]
    assert out[0]["held_out"] is None and out[0]["measure"] == "the net of the skipped sessions"
    assert out[1]["held_out"] == {
        "at": "2026-11-20T11:00:00-05:00",
        "verdict": "failed",
        "ci": [-5.0, 12.0],
        "use": 1,
    }


def test_one_automations_items():
    out = declared.items(
        [("idea", "idea", _idea_doc(), True), ("setting", "setting", _grid_doc(), False)], automation="c2"
    )
    assert [r["item"] for r in out] == ["stop_2x"]


def test_a_machine_with_no_results_has_none(tmp_path, monkeypatch):
    monkeypatch.setattr(
        declared.paths, "automations_dir", lambda licensed: tmp_path / ("lic" if licensed else "own")
    )
    assert declared.declared() == {"ok": True, "items": []}
