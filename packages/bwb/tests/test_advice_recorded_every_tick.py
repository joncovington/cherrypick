"""The session's advice decision is recorded on every in-session tick, not only on the entry
path (2026-09-17, the calendars finding applied suite-wide). Shown to fail without the preamble:
a tick that reaches no entry records nothing, and the advisor scores the day's artifact as never
having reached the loop."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime

from cherrypick.core import streamcache

from cherrypick.bwb import db, paper_loop


def _tick(monkeypatch, when: datetime, *, force: bool = False) -> list[str]:
    tmp = tempfile.mkdtemp()
    cache = os.path.join(tmp, "cache.db")
    streamcache.connect(cache).close()  # the schema, no rows: nothing to price, nothing to enter
    conn = db.connect(os.path.join(tmp, "paper.db"))
    calls: list[str] = []
    monkeypatch.setattr(
        paper_loop,
        "advice_decision",
        lambda config, day: calls.append(day) or {"day": day, "params": None, "experiments": []},
    )
    paper_loop.run_once({"symbols": ["SPX"], "defaults": {}}, conn, cache_path=cache, when=when, force=force)
    return calls


def test_an_in_session_tick_with_no_entry_still_records_the_decision(managed_home, monkeypatch):
    # 09:35 ET: in session, ahead of the entry time, so no entry path runs and only the preamble can record
    assert _tick(monkeypatch, datetime(2026, 9, 16, 9, 35)) == ["2026-09-16"]


def test_an_out_of_session_tick_records_nothing(managed_home, monkeypatch):
    assert _tick(monkeypatch, datetime(2026, 9, 16, 8, 0)) == []


def test_a_forced_tick_does_not_fix_the_day(managed_home, monkeypatch):
    """A forced run is for outside the gates; it must not be the process that records the day."""
    assert _tick(monkeypatch, datetime(2026, 9, 16, 8, 0), force=True) == []
