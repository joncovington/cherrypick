"""Pure crontab-editing logic for the POSIX scheduler backend (runs cross-platform — no subprocess)."""

from __future__ import annotations

import pytest

from cherrypick.orchestrator import tasks

CMD = '"/usr/bin/python3" "/opt/cherrypick/run.py" watchdog'
NAME = "cherrypick-watchdog"


def test_registry_snapshot_collects_every_declared_task(monkeypatch):
    seen = []

    def fake_query_verbose(name):
        seen.append(name)
        return {"exists": name != "cherrypick-earnings-paper-entry"}

    monkeypatch.setattr(tasks, "query_verbose", fake_query_verbose)
    cfg = {
        "modules": {
            "meic": {"enabled": True, "paper": {"task_name": "cherrypick-meic-paper-loop"}},
            "earnings": {
                "enabled": True,
                "paper": {
                    "entry_task_name": "cherrypick-earnings-paper-entry",
                    "exit_task_name": "cherrypick-earnings-paper-exit",
                    "dolt_service": {"task_name": "cherrypick-earnings-dolt"},
                },
            },
            "disabled_mod": {"enabled": False, "paper": {"task_name": "should-not-appear"}},
        },
        "watchdog": {"task_name": "cherrypick-watchdog"},
        "trade_notify": {"task_name": "cherrypick-trade-notify"},
    }
    snap = tasks.registry_snapshot(cfg)
    assert set(snap) == {
        "cherrypick-meic-paper-loop",
        "cherrypick-earnings-paper-entry",
        "cherrypick-earnings-paper-exit",
        "cherrypick-earnings-dolt",
        "cherrypick-watchdog",
        "cherrypick-trade-notify",
        "cherrypick-log-archive",  # monthly rotation, on by default too
    }
    assert snap["cherrypick-earnings-paper-entry"]["exists"] is False
    assert "should-not-appear" not in seen


def test_registry_snapshot_omits_log_archive_when_opted_out(monkeypatch):
    monkeypatch.setattr(tasks, "query_verbose", lambda name: {"exists": True})
    cfg = {"modules": {}, "log_archive": {"enabled": False}}
    assert "cherrypick-log-archive" not in tasks.registry_snapshot(cfg)


def test_registry_snapshot_includes_symbol_watch_when_enabled(monkeypatch):
    monkeypatch.setattr(tasks, "query_verbose", lambda name: {"exists": True})
    cfg = {"modules": {}, "symbol_watch": {"enabled": True}}
    assert "cherrypick-earnings-symbol-watch" in tasks.registry_snapshot(cfg)


def test_registry_snapshot_omits_symbol_watch_by_default(monkeypatch):
    monkeypatch.setattr(tasks, "query_verbose", lambda name: {"exists": True})
    cfg = {"modules": {}}
    assert "cherrypick-earnings-symbol-watch" not in tasks.registry_snapshot(cfg)


def test_monthly_schedule_cron_format():
    assert tasks._monthly_schedule(1, "03:30") == "30 3 1 * *"
    with pytest.raises(ValueError):
        tasks._monthly_schedule(31, "03:30")  # day must be 1..28


def test_allow_on_battery_noop_on_posix(monkeypatch):
    monkeypatch.setattr(tasks, "_IS_WINDOWS", False)
    assert tasks.allow_on_battery("cherrypick-watchdog") == {"ok": True, "detail": "n/a (posix)"}


def test_allow_on_battery_is_best_effort(monkeypatch):
    # A missing/failed PowerShell must never raise or invalidate the task creation that called it.
    monkeypatch.setattr(tasks, "_IS_WINDOWS", True)

    def boom(*a, **k):
        raise OSError("powershell not found")

    monkeypatch.setattr(tasks.subprocess, "run", boom)
    res = tasks.allow_on_battery("cherrypick-watchdog")
    assert res["ok"] is False
    assert "OSError" in res["detail"]


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
    text = tasks._cron_upsert(
        tasks._cron_upsert("", NAME, tasks._cron_line("*/10 * * * *", CMD, NAME)),
        "cherrypick-earnings-paper-entry",
        other,
    )
    out = tasks._cron_remove(text, NAME)
    assert not tasks._cron_has(out, NAME)
    assert tasks._cron_has(out, "cherrypick-earnings-paper-entry")  # sibling survives


def test_command_for_round_trips():
    text = tasks._cron_upsert("", NAME, tasks._cron_line("*/10 * * * *", CMD, NAME))
    assert tasks._cron_command_for(text, NAME) == f"{CMD} >/dev/null 2>&1"
    assert tasks._cron_command_for("", NAME) is None


# --- delete() is idempotent (Windows backend; monkeypatched so it runs cross-platform) -------------
def test_delete_absent_task_is_a_successful_noop(monkeypatch):
    """`install` calls delete() unconditionally to clear the stale fixed-time EOD digest/insight tasks.
    schtasks exits non-zero when there is nothing to delete, which used to surface as ok:false on every
    clean install and made install's top-level `ok` useless as a failure signal."""
    monkeypatch.setattr(tasks, "_IS_WINDOWS", True)
    monkeypatch.setattr(tasks, "exists", lambda name: False)

    def boom(*a, **kw):  # nothing should be shelled out for an absent task
        raise AssertionError("subprocess.run must not be called when the task does not exist")

    monkeypatch.setattr(tasks.subprocess, "run", boom)

    result = tasks.delete("cherrypick-eod-digest")
    assert result["ok"] is True
    assert "cherrypick-eod-digest" in result["detail"]


def test_delete_present_task_reports_the_schtasks_result(monkeypatch):
    monkeypatch.setattr(tasks, "_IS_WINDOWS", True)
    monkeypatch.setattr(tasks, "exists", lambda name: True)
    calls = []

    class _R:
        returncode = 0
        stdout = "SUCCESS: deleted"
        stderr = ""

    def fake_run(argv, **kw):
        calls.append(argv)
        return _R()

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)

    result = tasks.delete(NAME)
    assert result["ok"] is True
    assert result["detail"] == "SUCCESS: deleted"
    assert [a[1] for a in calls] == ["/End", "/Delete"]  # ended before deleting


def test_delete_present_task_propagates_a_real_failure(monkeypatch):
    monkeypatch.setattr(tasks, "_IS_WINDOWS", True)
    monkeypatch.setattr(tasks, "exists", lambda name: True)

    class _R:
        returncode = 1
        stdout = ""
        stderr = "ERROR: Access is denied."

    monkeypatch.setattr(tasks.subprocess, "run", lambda argv, **kw: _R())

    result = tasks.delete(NAME)
    assert result["ok"] is False
    assert "Access is denied" in result["detail"]


# --------------------------------------------------------------------------- windowed minute tasks
def test_windowed_minute_schedule_stays_inside_its_window():
    """The pre-open check runs 09:00-09:35 only. A global `*/2` would multiply every tick's work all
    day to cover 35 minutes."""
    assert tasks._windowed_minute_schedule(2, "09:00", "09:35") == "0-35/2 9 * * *"
    assert tasks._windowed_minute_schedule(5, "15:30", "15:55") == "30-55/5 15 * * *"


@pytest.mark.parametrize(
    "interval,start,end",
    [
        (2, "09:00", "10:35"),  # spans hours — would need a second cron line
        (2, "09:30", "09:30"),  # zero-length
        (2, "09:35", "09:00"),  # backwards
        (0, "09:00", "09:35"),  # interval out of range
        (60, "09:00", "09:35"),
    ],
)
def test_windowed_minute_schedule_refuses_what_it_cannot_express(interval, start, end):
    """Raising beats emitting a cron line that silently fires at the wrong times — this schedule
    guards a window that cannot be recovered once missed."""
    with pytest.raises(ValueError):
        tasks._windowed_minute_schedule(interval, start, end)
