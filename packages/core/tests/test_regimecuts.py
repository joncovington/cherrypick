"""cherrypick.core.regimecuts: the artifact contract shared by flies and MEIC. Pure functions over
rows, so every rule here is pinned on lists -- no ledger, no clock."""

import json

from cherrypick.core import regimecuts as rc


def _brk(date, scope="*", kind="entry_rules", reason="r"):
    return {"break_date": date, "scope": scope, "kind": kind, "reason": reason, "detail": None}


# --------------------------------------------------------------------------- era
def test_era_start_is_the_latest_book_wide_break_on_or_before_the_session():
    era = rc.era_bounds(
        [_brk("2026-08-11", kind="cadence"), _brk("2026-08-21", kind="cutover")], "2026-09-18"
    )
    assert era["start"] == "2026-08-21" and era["bounding_break"]["kind"] == "cutover"


def test_era_ignores_future_dated_breaks_until_they_pass():
    """Shown to fail with the `<= session` filter removed: the 12-18 gate would bound the era and
    every row would read as pre-era."""
    breaks = [_brk("2026-08-21", kind="cutover"), _brk("2026-11-27"), _brk("2026-12-18")]
    era = rc.era_bounds(breaks, "2026-09-18")
    assert era["start"] == "2026-08-21"
    assert [b["break_date"] for b in era["ignored_future"]] == ["2026-11-27", "2026-12-18"]
    # ...and once the session reaches it, it binds
    assert rc.era_bounds(breaks, "2026-12-18")["start"] == "2026-12-18"


def test_a_book_own_break_moves_only_that_book_start():
    """Shown to fail by returning the book-wide start for every book."""
    breaks = [_brk("2026-08-21", kind="cutover"), _brk("2026-08-31", scope="callwall", kind="arm_added")]
    era = rc.era_bounds(breaks, "2026-09-18")
    assert rc.book_start(era, "callwall") == ("2026-08-31", era["arm_starts"]["callwall"][1])
    assert rc.book_start(era, "control") == ("2026-08-21", None)


def test_a_book_break_before_the_book_wide_boundary_is_superseded():
    breaks = [_brk("2026-08-11", scope="sign", kind="arm_added"), _brk("2026-08-21", kind="cutover")]
    era = rc.era_bounds(breaks, "2026-09-18")
    assert rc.book_start(era, "sign") == ("2026-08-21", None)


def test_partial_session_breaks_never_bound_the_era_but_are_listed_as_caveats():
    breaks = [
        _brk("2026-08-11", kind="cutover"),
        _brk("2026-08-20", kind="partial_session", reason="lost morning"),
    ]
    era = rc.era_bounds(breaks, "2026-09-18")
    assert era["start"] == "2026-08-11"
    assert era["caveats"] == [
        {"break_date": "2026-08-20", "kind": "partial_session", "reason": "lost morning"}
    ]


def test_no_break_means_start_is_none_and_nothing_is_ignored():
    era = rc.era_bounds([], "2026-09-18")
    assert era["start"] is None and era["bounding_break"] is None and era["ignored_future"] == []
    assert rc.book_start(era, "control") == (None, None)


# --------------------------------------------------------------------------- assembly
def _row(bucket, sessions, trades=10, net=100.0, completed=None):
    out = {
        "bucket": bucket,
        "sessions": sessions,
        "trades": trades,
        "net_pnl": net,
        "gross_pnl": net,
        "fees": 0.0,
        "wins": trades,
        "losses": 0,
        "win_rate": 1.0,
        "avg_pnl": net / trades,
        "avg_win": None,
        "avg_loss": None,
        "fee_drag_pct": None,
        "profit_factor": None,
        "value_min": 0.1,
        "value_max": 0.9,
    }
    if completed is not None:
        out["completed"] = completed
        out["completion_rate"] = completed / trades
    return out


def _doc(**overrides):
    era = rc.era_bounds([_brk("2026-08-21", kind="cutover")], "2026-09-18")
    books = [
        {
            "book": "advised:x",
            "era_start": "2026-08-21",
            "era_break": None,
            "summary": {"sessions": 5, "trades": 20, "net_pnl": 50.0, "win_rate": 0.6},
            "coverage": {
                "gex": {
                    "coverage_pct": 100.0,
                    "tagged": 20,
                    "untagged": 0,
                    "sessions": 5,
                    "effective_n": 5,
                    "daily_scale": True,
                    "degenerate": False,
                    "underpowered": True,
                    "buckets": {"a": 20},
                }
            },
            "regimes": {
                "gex": [_row("untagged", 1, trades=1), _row("a", 2, trades=3), _row("b", 5, trades=16)]
            },
        },
        {
            "book": "control",
            "era_start": "2026-08-21",
            "era_break": None,
            "summary": {
                "sessions": 9,
                "trades": 40,
                "net_pnl": 500.0,
                "win_rate": 0.8,
                "completed": 30,
                "completion_rate": 0.75,
            },
            "coverage": {
                "gex": {
                    "coverage_pct": 100.0,
                    "tagged": 40,
                    "untagged": 0,
                    "sessions": 9,
                    "effective_n": 9,
                    "daily_scale": True,
                    "degenerate": False,
                    "underpowered": True,
                    "buckets": {"a": 40},
                }
            },
            "regimes": {"gex": [_row("a", 9, trades=40, completed=30)]},
        },
    ]
    kw = dict(
        module="flies",
        session="2026-09-18",
        symbol="SPX",
        book_column="arm",
        entry_modes=("legged",),
        phase="entry",
        era=era,
        books=books,
        cross_tabs=[],
        generated_at="2026-09-18T16:40:00-04:00",
        min_effective_n=14,
    )
    kw.update(overrides)
    return rc.assemble(**kw)


def test_thin_is_stamped_by_the_writer_below_three_sessions():
    """Shown to fail by flipping `<` to `<=` in `_cell`: the three-session bucket would read thin."""
    doc = _doc()
    advised = next(b for b in doc["books"] if b["book"] == "advised:x")
    by = {b["bucket"]: b for b in advised["dimensions"]["gex"]["buckets"]}
    assert by["untagged"]["thin"] is True and by["a"]["thin"] is True and by["b"]["thin"] is False
    assert doc["thin_below_sessions"] == 3


def test_books_and_buckets_are_ordered_deterministically():
    doc = _doc()
    assert [b["book"] for b in doc["books"]] == ["control", "advised:x"]
    advised = next(b for b in doc["books"] if b["book"] == "advised:x")
    assert [b["bucket"] for b in advised["dimensions"]["gex"]["buckets"]] == ["b", "a", "untagged"]
    assert json.dumps(_doc(), sort_keys=False) == json.dumps(_doc(), sort_keys=False)


def test_completion_fields_are_null_for_a_module_without_the_concept():
    doc = _doc()
    advised = next(b for b in doc["books"] if b["book"] == "advised:x")
    control = next(b for b in doc["books"] if b["book"] == "control")
    assert advised["completed"] is None and advised["completion_rate"] is None
    assert advised["dimensions"]["gex"]["buckets"][0]["completed"] is None
    assert (
        control["completion_rate"] == 0.75 and control["dimensions"]["gex"]["buckets"][0]["completed"] == 30
    )


def test_the_document_carries_the_contract_fields_and_the_era():
    doc = _doc()
    assert (
        doc["cut_version"] == rc.CUT_VERSION and doc["module"] == "flies" and doc["session"] == "2026-09-18"
    )
    assert doc["era"]["start"] == "2026-08-21" and doc["era"]["bounding_break"]["kind"] == "cutover"
    assert doc["entry_modes"] == ["legged"] and doc["min_effective_n"] == 14
    # coverage's own bucket counts are dropped: the bucket rows carry them
    assert "buckets" in doc["books"][0]["dimensions"]["gex"]
    assert "tagged" in doc["books"][0]["dimensions"]["gex"]


def test_cross_tab_cells_are_thin_stamped_and_ordered():
    doc = _doc(
        cross_tabs=[
            {
                "dims": ["gex", "trend"],
                "books": [
                    {
                        "book": "control",
                        "cells": [
                            {"buckets": ["a", "untagged"], "sessions": 6, "trades": 4, "net_pnl": 1.0},
                            {"buckets": ["a", "flat"], "sessions": 2, "trades": 9, "net_pnl": -812.0},
                            {"buckets": ["a", "up"], "sessions": 7, "trades": 5, "net_pnl": 3.0},
                        ],
                    }
                ],
            }
        ]
    )
    cells = doc["cross_tabs"][0]["books"][0]["cells"]
    assert [c["buckets"] for c in cells] == [["a", "flat"], ["a", "up"], ["a", "untagged"]]
    assert cells[0]["thin"] is True and cells[1]["thin"] is False


# --------------------------------------------------------------------------- files
def test_write_json_atomic_leaves_no_tmp_and_replaces_in_place(tmp_path):
    target = tmp_path / "sub" / "x.json"
    rc.write_json_atomic(target, {"a": 1})
    rc.write_json_atomic(target, {"a": 2})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 2}
    assert sorted(p.name for p in target.parent.iterdir()) == ["x.json"]


def test_an_older_session_never_replaces_the_latest_copy_and_a_newer_one_does(tmp_path):
    """Shown to fail by copying to regime_cuts.json unconditionally: a backfill of 09-10 would
    overwrite the 09-18 cut the console and the pack read."""
    rc.write_artifact(tmp_path, {"session": "2026-09-18", "v": "new"})
    out = rc.write_artifact(tmp_path, {"session": "2026-09-10", "v": "old"})
    assert out["latest"] is None and rc.latest_session(tmp_path) == "2026-09-18"
    assert rc.dated_sessions(tmp_path) == ["2026-09-10", "2026-09-18"]
    out = rc.write_artifact(tmp_path, {"session": "2026-09-19", "v": "newer"})
    assert out["latest"] is not None and rc.latest_session(tmp_path) == "2026-09-19"
    assert rc.write_artifact(tmp_path, {"session": "2026-09-19", "v": "rerun"})["latest"] is not None


def test_latest_session_is_none_when_absent_or_unreadable(tmp_path):
    assert rc.latest_session(tmp_path) is None
    (tmp_path / rc.LATEST_NAME).write_text("not json", encoding="utf-8")
    assert rc.latest_session(tmp_path) is None
    assert rc.dated_sessions(tmp_path / "missing") == []
