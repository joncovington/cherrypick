"""Pure crontab-editing logic for the POSIX scheduler backend (runs cross-platform — no subprocess)."""

from __future__ import annotations

import pytest

from cherrypick.orchestrator import tasks

CMD = '"/usr/bin/python3" "/opt/cherrypick/run.py" watchdog'
NAME = "cherrypick-watchdog"


def test_minute_schedule_common_cadences():
    assert tasks._minute_schedule(2) == "*/2 * * * *"
    assert tasks._minute_schedule(10) == "*/10 * * * *"
    assert tasks._minute_schedule(30) == "*/30 * * * *"


@pytest.mark.parametrize("bad", [0, 60, 1440, -5])
def test_minute_schedule_rejects_out_of_range(bad):
    with pytest.raises(ValueError):
        tasks._minute_schedule(bad)


def test_daily_schedule():
    assert tasks._daily_schedule("15:45") == "45 15 * * *"
    assert tasks._daily_schedule("09:05") == "5 9 * * *"
    with pytest.raises(ValueError):
        tasks._daily_schedule("24:00")


def test_cron_line_has_schedule_command_and_marker():
    line = tasks._cron_line("*/10 * * * *", CMD, NAME)
    assert line.startswith("*/10 * * * * ")
    assert CMD in line
    assert line.endswith(f"# cherrypick:{NAME}")
    assert ">/dev/null 2>&1" in line


def test_upsert_appends_when_absent_and_preserves_foreign_lines():
    existing = "0 0 * * * /home/me/backup.sh # my own job\n"
    line = tasks._cron_line("*/10 * * * *", CMD, NAME)
    out = tasks._cron_upsert(existing, NAME, line)
    assert "backup.sh # my own job" in out  # untouched
    assert tasks._cron_has(out, NAME)
    assert out.endswith("\n")


def test_upsert_replaces_existing_managed_line_no_duplicate():
    line1 = tasks._cron_line("*/10 * * * *", CMD, NAME)
    line2 = tasks._cron_line("*/2 * * * *", CMD, NAME)
    out = tasks._cron_upsert(tasks._cron_upsert("", NAME, line1), NAME, line2)
    assert out.count(f"# cherrypick:{NAME}") == 1
    assert "*/2 * * * *" in out and "*/10 * * * *" not in out


def test_remove_only_targets_named_entry():
    other = tasks._cron_line("0 9 * * *", CMD, "cherrypick-earnings-paper-entry")
    text = tasks._cron_upsert(tasks._cron_upsert("", NAME, tasks._cron_line("*/10 * * * *", CMD, NAME)),
                              "cherrypick-earnings-paper-entry", other)
    out = tasks._cron_remove(text, NAME)
    assert not tasks._cron_has(out, NAME)
    assert tasks._cron_has(out, "cherrypick-earnings-paper-entry")  # sibling survives


def test_command_for_round_trips():
    text = tasks._cron_upsert("", NAME, tasks._cron_line("*/10 * * * *", CMD, NAME))
    assert tasks._cron_command_for(text, NAME) == f"{CMD} >/dev/null 2>&1"
    assert tasks._cron_command_for("", NAME) is None
