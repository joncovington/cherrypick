"""Half days (an NYSE early close, 13:00 ET) are not tradeable: no arm enters, and an arm whose window
falls after the close is missed rather than left pending."""

from __future__ import annotations

import pytest
import test_paper_loop as base

from cherrypick.automations import db

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
    out = base.tick(w, base.at(10, 5, HALF))  # the window closes unentered
    entry = db.entry(w.conn, "iron-fly-1000", "SPX", HALF)
    assert out["missed"] == {"iron-fly-1000": "missed"}
    assert entry["outcome"] == "missed" and entry["refusal"] == "half_day_no_entries"


def test_an_arm_whose_window_is_after_the_close_is_missed_not_left_pending(tmp_path, half_day):
    """The 13:45 window opens 45 minutes after a 13:00 close, so no tick of its own ever comes. The
    first tick past the close marks it missed with the half-day reason."""
    w = base.world(tmp_path, keep=("iron-fly-1000", "iron-condor-1345"))
    base.tick(w, base.at(12, 59, HALF))
    assert db.entry(w.conn, "iron-condor-1345", "SPX", HALF)["outcome"] == "pending"
    out = base.tick(w, base.at(13, 1, HALF))
    assert out["skipped"] == "outside_rth"
    assert out["missed"] == {"iron-condor-1345": "missed"}
    row = db.entry(w.conn, "iron-condor-1345", "SPX", HALF)
    assert row["outcome"] == "missed" and row["refusal"] == "half_day_no_entries"


def test_an_ordinary_day_after_the_close_marks_nothing(tmp_path):
    """The control: only an early close takes the after-the-close path."""
    w = base.world(tmp_path)
    out = base.tick(w, base.at(16, 1))
    assert out["skipped"] == "outside_rth" and "missed" not in out


def test_an_ordinary_day_still_enters(tmp_path):
    w = base.world(tmp_path)
    assert base.tick(w, base.at(10, 0))["entries"] == {"iron-fly-1000": "entered"}
