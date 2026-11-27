"""Half days (an NYSE early close, 13:00 ET) are not tradeable: no arm enters, and an arm whose window
falls after the close is missed rather than left pending."""

from __future__ import annotations

import pytest
import test_paper_loop as base

from cherrypick.automations import db, entries

HALF = "2026-11-27"  # the day after Thanksgiving
HALF_NEXT = "2026-11-30"


@pytest.fixture
def half_day(monkeypatch):
    monkeypatch.setattr(base, "DAY", HALF)
    monkeypatch.setattr(base, "NEXT", HALF_NEXT)


def test_an_arm_in_its_window_on_a_half_day_does_not_enter(tmp_path, half_day):
    """The guard: a market the arm would enter, in its own window, and still no entry. Remove the
    early-close check in `engine.decide` and this opens a position."""
    w = base.world(tmp_path)
    out = base.tick(w, base.at(10, 0, HALF))
    assert out["entries"] == {"iron-fly-1000": "half_day_no_entries"}
    assert db.open_position_count(w.conn) == 0
    entry = db.entry(w.conn, "iron-fly-1000", "SPX", HALF)
    assert entry["outcome"] == "missed" and entry["refusal"] == "half_day_no_entries"


def test_an_arm_whose_window_is_after_the_close_is_missed_while_the_loop_runs(tmp_path, half_day):
    """The 13:45 window opens 45 minutes after a 13:00 close and the resident loop stops at the close, so
    no tick of that arm's own ever comes. The ticks that do run mark it missed with the half-day reason.
    Remove `day_over` from the tick and it stays pending for good."""
    w = base.world(tmp_path, keep=("iron-fly-1000", "iron-condor-1345"))
    out = base.tick(w, base.at(12, 59, HALF))  # the last minute the loop ticks
    assert out["missed"]["iron-condor-1345"] == "missed"
    row = db.entry(w.conn, "iron-condor-1345", "SPX", HALF)
    assert row["outcome"] == "missed" and row["refusal"] == "half_day_no_entries"


def test_the_refusal_is_filed_as_a_limit_after_the_trigger():
    """The funnel counts the trigger before the limits: `half_day_no_entries` is a limit, and the engine
    checks it after `entry_not_due`, so an arm not yet due still says so."""
    assert entries.step("half_day_no_entries") == "limits"
    assert entries.step("entry_not_due") == "trigger"


def test_an_ordinary_day_marks_nothing_early(tmp_path):
    """The control: a full day leaves a window not yet reached pending, and an arm in its window enters."""
    w = base.world(tmp_path, keep=("iron-fly-1000", "iron-condor-1345"))
    out = base.tick(w, base.at(10, 0))
    assert out["entries"].get("iron-fly-1000") == "entered"
    assert db.entry(w.conn, "iron-condor-1345", "SPX", base.DAY)["outcome"] == "pending"
    assert "iron-condor-1345" not in out["missed"]
