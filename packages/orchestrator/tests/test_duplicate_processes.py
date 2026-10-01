"""The duplicate-process check: every other guard, checked against the OS process list (2026-10-01)."""

from __future__ import annotations

from cherrypick.orchestrator import watchdog as wd

SIGS = {
    "flies-paper": wd._norm_cmd("-m cherrypick.flies.paper_loop --interval 15"),
    "flies-live": wd._norm_cmd("-m cherrypick.flies.live_loop --once --live"),
    "status-digest": wd._norm_cmd("C:\\repo\\run.py notify-status"),
    "console": wd._norm_cmd("C:\\repo\\packages\\console\\run.py dashboard --serve"),
}


def _p(pid, cmd, ppid=1):
    return {"pid": pid, "ppid": ppid, "cmd": cmd}


def test_two_copies_of_a_job_are_found():
    procs = [
        _p(10, '"C:\\Py\\pythonw.exe" -m cherrypick.flies.paper_loop --interval 15'),
        _p(11, "python -m cherrypick.flies.paper_loop --interval 15"),  # a manual launch beside it
    ]
    assert wd._duplicate_groups(procs, SIGS) == {"flies-paper": [10, 11]}


def test_one_copy_is_not_a_duplicate():
    assert wd._duplicate_groups([_p(10, "pythonw -m cherrypick.flies.paper_loop --interval 15")], SIGS) == {}


def test_a_launcher_and_its_child_are_one_instance():
    procs = [
        _p(20, '"pythonw.exe" C:\\repo\\packages\\console\\run.py dashboard --serve'),
        _p(21, '"pythonw.exe" C:\\repo\\packages\\console\\run.py dashboard --serve', ppid=20),
    ]
    assert wd._duplicate_groups(procs, SIGS) == {}


def test_a_longer_command_is_a_different_job():
    """`notify-status --close` is the close card, not a second hourly digest."""
    procs = [
        _p(30, "pythonw C:\\repo\\run.py notify-status"),
        _p(31, "pythonw C:\\repo\\run.py notify-status --close"),
    ]
    assert wd._duplicate_groups(procs, SIGS) == {}


def test_a_duplicate_live_loop_is_critical(monkeypatch):
    monkeypatch.setattr(
        wd,
        "_list_processes",
        lambda: [
            _p(40, "pythonw -m cherrypick.flies.live_loop --once --live"),
            _p(41, "python -m cherrypick.flies.live_loop --once --live"),
        ],
    )

    class Spec:
        def __init__(self, id, argv):
            self.id, self.argv = id, argv

    monkeypatch.setattr(
        wd.jobspec,
        "derive_jobs",
        lambda *a, **k: (
            [Spec("flies-live", ["pythonw", "-m", "cherrypick.flies.live_loop", "--once", "--live"])],
            {},
        ),
    )
    [finding] = wd._check_duplicate_processes({})
    assert finding.status == wd.CRITICAL and "40, 41" in finding.message


def test_cannot_say_is_no_finding(monkeypatch):
    monkeypatch.setattr(wd, "_list_processes", lambda: None)
    assert wd._check_duplicate_processes({}) == []
