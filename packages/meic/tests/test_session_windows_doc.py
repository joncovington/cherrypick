"""The session windows in docs/strategy.md are the ones the loops classify by.

The agent-driven entry path reads its windows from the docs (`.claude/commands/execute-entry.md`
quotes them), while paper, live and replay classify by `paper_loop._session_quality`. On 2026-10-19 a
documentation pass moved the docs to 09:30-10:00 and 14:30-15:30 while the code said 10:15 and 14:45,
so the two paths disagreed by fifteen minutes at each end. This reads the table and checks every row
against the function, at its first minute and its last.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from cherrypick.meic.paper_loop import _session_quality

DOCS = Path(__file__).resolve().parents[1] / "docs" / "strategy.md"
ROW = re.compile(r"^\|\s*(\d{2}):(\d{2})[–-](\d{2}):(\d{2})\s*\|\s*`(\w+)`\s*\|", re.M)


def _at(h: int, m: int) -> datetime:
    return datetime(2026, 10, 19, h, m)


def test_every_window_in_the_strategy_doc_is_the_one_the_loops_use():
    rows = ROW.findall(DOCS.read_text(encoding="utf-8"))
    assert len(rows) >= 5, "the session-window table in docs/strategy.md was not found"
    for h1, m1, h2, m2, label in rows:
        start, end = int(h1) * 60 + int(m1), int(h2) * 60 + int(m2)
        last = end - 1
        assert _session_quality(_at(start // 60, start % 60)) == label, f"{label} at {h1}:{m1}"
        assert _session_quality(_at(last // 60, last % 60)) == label, f"{label} just before {h2}:{m2}"
