"""What a supervised child says on the way out, and restarts someone asked for (2026-10-01).

Every child's stderr was the null device: nine console restarts and a collector's exit 1 on
2026-09-30 left nothing behind but a code. Each job's stderr now goes to its own file, a failed
exit's last lines reach supervisor.log and the registry (masked), and a restart marked as requested
is not a failure and not churn.
"""

from __future__ import annotations

import json
import time

from test_supervisor import MONDAY_NOON, base_cfg, flies_cfg, spawned  # noqa: F401 -- the fixture

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import supervisor


def _say(job_id: str, text: str) -> None:
    """Write to a job's stderr file the way its child would: appended, after the launch header."""
    with supervisor.stderr_log_path(job_id).open("ab") as fh:
        fh.write(text.encode())


def _watchdog(spawned):  # noqa: F811 -- pytest passes the fixture by name
    return next(p for p in spawned if "watchdog" in p.argv)


def test_a_childs_stderr_goes_to_its_own_file_with_a_launch_header(spawned):  # noqa: F811
    supervisor.Supervisor(base_cfg()).pass_once(now=MONDAY_NOON)
    handle = _watchdog(spawned).kw["stderr"]
    assert handle is not __import__("subprocess").DEVNULL, "child stderr is still discarded"
    assert handle.closed  # the supervisor keeps no copy past launch
    text = supervisor.stderr_log_path("watchdog").read_text(encoding="utf-8")
    assert text.startswith("--- ") and "start:" in text and "watchdog" in text


def test_a_failed_exit_puts_its_masked_tail_in_the_registry_and_the_log(spawned):  # noqa: F811
    sup = supervisor.Supervisor(base_cfg())
    sup.pass_once(now=MONDAY_NOON)
    # The shape a broker error takes in a traceback: the response body, quoted.
    _say(
        "watchdog",
        'Traceback (most recent call last):\n  File "x.py", line 1\n'
        'RuntimeError: order refused: {"account_number": "5WT99991", "reason": "bp"}\n',
    )
    _watchdog(spawned).exit(1)
    sup._state["watchdog"]["next_run_epoch"] = 0
    sup.pass_once(now=MONDAY_NOON)

    err = sup._state["watchdog"]["last_error"]
    assert (
        err.splitlines()[-1] == 'RuntimeError: order refused: {"account_number": "****9991", "reason": "bp"}'
    )
    assert "5WT99991" not in err
    log = cfgmod.log_file("supervisor.log").read_text(encoding="utf-8")
    assert "watchdog: stderr (last lines):" in log and "****9991" in log and "5WT99991" not in log
    reg = json.loads(supervisor.jobs_path().read_text(encoding="utf-8"))
    assert reg["jobs"]["watchdog"]["last_error"] == err


def test_only_this_runs_output_is_reported(spawned):  # noqa: F811
    """A previous run's traceback sits earlier in the same file; it must not be blamed on this one."""
    sup = supervisor.Supervisor(base_cfg())
    sup.pass_once(now=MONDAY_NOON)
    _say("watchdog", "ValueError: yesterday's problem\n")
    _watchdog(spawned).exit(0)
    sup._state["watchdog"]["next_run_epoch"] = 0
    sup.pass_once(now=MONDAY_NOON)  # respawns: a new header and offset
    second = [p for p in spawned if "watchdog" in p.argv][-1]
    second.exit(1)
    sup._state["watchdog"]["next_run_epoch"] = 0
    sup.pass_once(now=MONDAY_NOON)
    assert "last_error" not in sup._state["watchdog"]  # this run said nothing


def test_a_clean_exit_clears_the_last_error(spawned):  # noqa: F811
    sup = supervisor.Supervisor(base_cfg())
    sup.pass_once(now=MONDAY_NOON)
    _say("watchdog", "OSError: disk full\n")
    _watchdog(spawned).exit(1)
    sup._state["watchdog"]["next_run_epoch"] = 0
    sup.pass_once(now=MONDAY_NOON)
    assert "last_error" in sup._state["watchdog"]
    sup._state["watchdog"]["backoff_until"] = time.time() - 1
    sup._state["watchdog"]["next_run_epoch"] = 0
    sup.pass_once(now=MONDAY_NOON)
    [p for p in spawned if "watchdog" in p.argv][-1].exit(0)
    sup._state["watchdog"]["next_run_epoch"] = 0
    sup.pass_once(now=MONDAY_NOON)
    assert "last_error" not in sup._state["watchdog"]


def test_an_adopted_childs_output_is_still_read_after_a_supervisor_restart(spawned):  # noqa: F811
    """A file, not a pipe, so a child outlives the supervisor that launched it and keeps writing;
    the offset rides the registry, so the next supervisor reads the right run."""
    supervisor.Supervisor(base_cfg()).pass_once(now=MONDAY_NOON)
    reg = json.loads(supervisor.jobs_path().read_text(encoding="utf-8"))
    reg["jobs"]["watchdog"]["running_pid"] = 999999999  # gone by the time the new one looks
    supervisor.jobs_path().write_text(json.dumps(reg), encoding="utf-8")
    _say("watchdog", "MemoryError: the reason it died\n")

    sup = supervisor.Supervisor(base_cfg())
    sup.adopt_prior_state()
    sup._state["watchdog"]["running_pid"] = 999999999
    sup._state["watchdog"]["next_run_epoch"] = 0
    sup.pass_once(now=MONDAY_NOON)
    assert sup._state["watchdog"]["last_error"] == "MemoryError: the reason it died"


# --------------------------------------------------------------------------- requested restarts


def test_a_requested_restart_is_not_a_failure(spawned):  # noqa: F811
    sup = supervisor.Supervisor(base_cfg())
    sup.pass_once(now=MONDAY_NOON)
    supervisor.request_restart("watchdog", by="test")
    _watchdog(spawned).exit(1)  # what a taskkill looks like
    sup._state["watchdog"]["next_run_epoch"] = 0
    res = sup.pass_once(now=MONDAY_NOON)

    st = sup._state["watchdog"]
    assert not st.get("consecutive_failures") and st["backoff_until"] is None
    assert "watchdog" in res["started"]  # straight back
    assert not supervisor.restart_request_path("watchdog").exists()  # consumed
    assert "restart requested by test" in cfgmod.log_file("supervisor.log").read_text(encoding="utf-8")


def test_a_requested_restart_of_a_resident_is_not_churn(spawned, tmp_path):  # noqa: F811
    sup = supervisor.Supervisor(flies_cfg(tmp_path))
    sup.pass_once(now=MONDAY_NOON)
    assert sup._state["flies-paper"]["starts_in_window"] == 1
    supervisor.request_restart("flies-paper", by="test")
    next(p for p in spawned if "--interval" in p.argv).exit(1)
    sup.pass_once(now=MONDAY_NOON)
    assert len([p for p in spawned if "--interval" in p.argv]) == 2  # restarted
    assert sup._state["flies-paper"]["starts_in_window"] == 1  # but not counted as churn


def test_an_unmarked_kill_still_reads_as_a_failure(spawned):  # noqa: F811
    sup = supervisor.Supervisor(base_cfg())
    sup.pass_once(now=MONDAY_NOON)
    _watchdog(spawned).exit(1)
    sup._state["watchdog"]["next_run_epoch"] = 0
    sup.pass_once(now=MONDAY_NOON)
    assert sup._state["watchdog"]["consecutive_failures"] == 1


def test_a_stale_request_does_not_excuse_a_later_crash(spawned):  # noqa: F811
    """A request whose kill never happened must not hide a real crash hours later."""
    sup = supervisor.Supervisor(base_cfg())
    sup.pass_once(now=MONDAY_NOON)
    path = supervisor.restart_request_path("watchdog")
    path.write_text(json.dumps({"requested_at": time.time() - 3600, "by": "old"}), encoding="utf-8")
    _watchdog(spawned).exit(1)
    sup._state["watchdog"]["next_run_epoch"] = 0
    sup.pass_once(now=MONDAY_NOON)
    assert sup._state["watchdog"]["consecutive_failures"] == 1
    assert not path.exists()  # and it is cleared, not left to excuse the next one


# (restart-console's own behaviour -- it now asks the supervisor and kills nothing -- is covered in
# test_restart_console.py and test_holds_and_restarts.py.)
