"""Every log source must reach the log card, and the merge must be chronological.

Two independent bugs made the card show one module and nothing else:

  * `_parse_log_line` only dated JSON lines. meic and flies write plain text with the stamp inline,
    so they parsed as undated — which the sort deliberately places last, and the newest-N slice then
    kept *only* those. The watchdog, notify and earnings sources were pushed out entirely no matter
    how recent they were.
  * earnings' log was resolved to its module directory, but earnings is `cherrypick_scheduled`: it
    has no loop, so the orchestrator runs its passes and writes the record to the suite logs root
    instead. It was invisible while a current 240KB log sat one directory up.

Both are pinned here because both were silent — the card rendered happily, just incompletely.
"""

from __future__ import annotations

import pytest

from cherrypick.orchestrator import dashboard

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "name,raw,want_level",
    [
        # flies: bracketed ISO stamp, no level word
        ("flies bracketed", "[2026-07-31T21:00:01] 2026-07-31 settled — idle", "INFO"),
        # meic: bare stamp followed by a level word
        ("meic bare + level", "2026-08-02 00:40:01 INFO outside trading window", "INFO"),
        ("meic WARNING maps to WARN", "2026-08-02 00:41:00 WARNING streamer stale", "WARN"),
        ("meic CRITICAL", "2026-08-02 00:42:00 CRITICAL loop died", "CRITICAL"),
    ],
)
def test_plain_text_lines_are_dated(name, raw, want_level):
    e = dashboard._parse_log_line("mod", raw)
    assert e["ts"], f"{name}: must yield a timestamp or it loses the merge"
    assert e["level"] == want_level, name
    # The stamp itself must be gone. flies legitimately repeats the session date in its message
    # body ("[stamp] 2026-07-31 settled ..."), so a leading date is not evidence it was missed.
    assert raw.startswith("[") is False or not e["text"].startswith("["), name
    assert e["ts"] not in e["text"], f"{name}: the stamp should not remain in the text"


def test_json_lines_are_still_dated():
    e = dashboard._parse_log_line("earnings", '{"ts": "2026-08-02T19:45:00+00:00", "phase": "entry"}')
    assert e["ts"] == "2026-08-02T19:45:00+00:00"
    assert e["text"] == "entry"


def test_a_line_with_no_stamp_is_undated_and_kept_verbatim():
    """Traceback bodies and bare continuations are not entries; they must not acquire a fake time,
    and the sort puts them last."""
    e = dashboard._parse_log_line("meic", '  File "x.py", line 14, in <module>')
    assert e["ts"] is None
    assert "File" in e["text"]


def test_dated_lines_are_not_crowded_out_by_undated_ones():
    """The regression: undated entries sort last, so a newest-N slice kept only them."""
    entries = [dashboard._parse_log_line("meic", "  traceback continuation") for _ in range(50)]
    tick = '{"ts": "2026-08-02T21:12:02+00:00", "kind": "tick"}'
    entries.append(dashboard._parse_log_line("watchdog", tick))
    entries.sort(key=lambda e: (e["ts"] is None, e["ts"] or ""))
    assert entries[0]["source"] == "watchdog", "dated lines must survive ahead of undated ones"


def test_module_log_resolves_to_the_suite_root_when_the_orchestrator_writes_it(tmp_path, monkeypatch):
    """earnings is cherrypick_scheduled: the orchestrator logs its passes centrally, so the reader
    must look in the suite root too — while a module's own directory keeps precedence."""
    monkeypatch.setattr(dashboard.cfgmod, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(dashboard.cfgmod, "module_logs_dir", lambda n: tmp_path / n)

    (tmp_path / "earnings").mkdir()
    suite_log = tmp_path / "earnings_paper.log"
    suite_log.write_text("{}", encoding="utf-8")
    assert dashboard._resolve_module_log("earnings", "logs/earnings_paper.log") == suite_log

    own = tmp_path / "earnings" / "earnings_paper.log"
    own.write_text("{}", encoding="utf-8")
    assert dashboard._resolve_module_log("earnings", "logs/earnings_paper.log") == own, (
        "a module's own log directory must win when it has one"
    )
