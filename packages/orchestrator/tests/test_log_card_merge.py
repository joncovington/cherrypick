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

from datetime import datetime, timedelta, timezone

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


def test_timestamps_are_comparable_across_sources():
    """Raw string sort is wrong twice over: the sources use different separators (a space sorts
    before "T") and different zones (meic writes naive local, the JSON sources write UTC).

    Derived from a real instant rather than hardcoded strings -- an earlier version of this test
    baked in the author's UTC-6 offset and failed in CI, which runs UTC. The rule under test is
    "naive means local", so the fixture has to be built the same way on whatever machine runs it.
    """
    instant = datetime(2026, 8, 2, 21, 42, 2, tzinfo=timezone.utc)
    utc_text = instant.isoformat()  # what watchdog/notify write
    local_naive = instant.astimezone().strftime("%Y-%m-%d %H:%M:%S")  # what meic writes

    assert local_naive.replace(" ", "T") != utc_text, "fixtures should differ in text"
    assert abs(dashboard._ts_key(local_naive)[1] - dashboard._ts_key(utc_text)[1]) < 2, (
        "the same instant must compare equal once the zone is applied"
    )


def test_a_space_separator_would_sort_before_a_T_separator():
    """The text-level defect the instant comparison removes, stated without any zone dependency."""
    assert "2026-08-02 15:42:02" < "2026-08-02T15:42:02"


def test_undated_and_unparseable_stamps_sort_last():
    assert dashboard._ts_key(None)[0] == 1
    assert dashboard._ts_key("not a timestamp")[0] == 1
    assert dashboard._ts_key("2026-08-02T21:42:02+00:00")[0] == 0


def test_ordering_is_by_instant_not_by_text():
    earlier = datetime(2026, 8, 2, 21, 0, 0, tzinfo=timezone.utc)
    later = earlier + timedelta(hours=1)
    later_local_naive = later.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    assert dashboard._ts_key(later_local_naive) > dashboard._ts_key(earlier.isoformat())


def test_the_shared_core_logs_format_keeps_its_offset():
    """cherrypick.core.logs writes "<ISO with offset> LEVEL message". The offset must land in `ts`,
    not stranded at the front of the message — that would revert the stamp to naive, which is the
    ambiguity the shared writer exists to remove."""
    e = dashboard._parse_log_line("flies", "2026-08-02T15:54:21-06:00 INFO settled at 748.97")
    assert e["ts"] == "2026-08-02T15:54:21-06:00"
    assert e["level"] == "INFO"
    assert e["text"] == "settled at 748.97"
    assert datetime.fromisoformat(e["ts"]).tzinfo is not None


def test_legacy_shapes_still_parse():
    """Months of history predate the shared writer and must stay readable."""
    old_flies = dashboard._parse_log_line("flies", "[2026-07-31T21:00:01] settled - idle")
    old_meic = dashboard._parse_log_line("meic", "2026-08-02 00:40:01 INFO outside trading window")
    assert old_flies["ts"] and old_meic["ts"]
    assert old_meic["text"] == "outside trading window"


def test_a_utc_offset_line_is_ordered_against_a_local_offset_line_correctly():
    earlier = dashboard._parse_log_line("a", "2026-08-02T15:54:21-06:00 INFO earlier")  # 21:54Z
    later = dashboard._parse_log_line("b", "2026-08-02T22:00:00+00:00 INFO later")
    assert dashboard._ts_key(earlier["ts"]) < dashboard._ts_key(later["ts"])
