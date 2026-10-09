"""The earnings-calendar read after a Dolt pull, and the watchdog warning built on it.

On 2026-09-27 a pull succeeded but the calendar read straight after it failed once; the null it
wrote sat in the state file until the next day's pull, so the watchdog warned every hour about a
calendar that answered fine minutes later -- and could not say why, because the error was
swallowed. These pin the fix: the read retries, a failure is recorded with its reason, and the
warning quotes it and names the way to clear it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import watchdog

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "refresh_dolt_data.py"


def _module():
    spec = importlib.util.spec_from_file_location("refresh_dolt_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rdd = _module()


def _flaky(*answers):
    calls = iter(answers)

    def read():
        answer = next(calls)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return read


def test_a_read_that_fails_once_after_a_pull_is_retried():
    read = _flaky(ConnectionError("server settling"), "2026-11-06")
    assert rdd.calendar_max_date(read, attempts=3, pause=lambda: None) == ("2026-11-06", None)


def test_a_read_that_keeps_failing_records_why():
    read = _flaky(*(ConnectionError("refused"),) * 3)
    value, error = rdd.calendar_max_date(read, attempts=3, pause=lambda: None)
    assert value is None and error == "ConnectionError: refused"


def test_an_empty_calendar_is_an_error_not_a_date():
    value, error = rdd.calendar_max_date(_flaky(None, None), attempts=2, pause=lambda: None)
    assert value is None and "empty" in error


def _state(**fields):
    path = cfgmod.state_file("dolt_data.json")  # where the watchdog reads it
    path.write_text(json.dumps({"refreshed_at": "2026-09-27T16:13:30+00:00", **fields}), encoding="utf-8")


def _calendar_finding():
    cfg = {"capabilities": {"dolt": True}, "modules": {"earnings": {"enabled": True}}}
    found = [f for f in watchdog._check_earnings_calendar(cfg) if f.key == "earnings.calendar"]
    return found[0] if found else None


def test_the_warning_quotes_the_recorded_error_and_names_the_recheck():
    _state(earnings_calendar_max_date=None, earnings_calendar_error="ConnectionError: refused")
    finding = _calendar_finding()
    assert finding is not None and finding.status == watchdog.WARN
    assert "ConnectionError: refused" in finding.message and "--recheck" in finding.message


# --------------------------------------------------------------------------- compaction


def _clone(tmp_path: Path, name: str, loose: int, archived: int = 1000) -> Path:
    noms = tmp_path / name / ".dolt" / "noms"
    (noms / "oldgen").mkdir(parents=True)
    (noms / ("a" * 32)).write_bytes(b"x" * loose)  # a table file a pull left
    (noms / "oldgen" / f"{'b' * 32}.darc").write_bytes(b"x" * archived)  # already compacted
    (noms / "manifest").write_bytes(b"x" * 50)
    return tmp_path / name


def test_loose_bytes_counts_table_files_not_archives_or_the_manifest(tmp_path):
    repo = _clone(tmp_path, "stocks", loose=700, archived=5000)
    assert rdd.loose_bytes(repo) == 700
    assert rdd.loose_bytes(tmp_path / "missing") == 0


def test_only_a_clone_past_the_threshold_is_compacted(tmp_path):
    _clone(tmp_path, "stocks", loose=900)
    _clone(tmp_path, "earnings", loose=100)
    ran = []
    out = rdd.compact_loose(tmp_path, ["stocks", "earnings"], threshold=500, gc=ran.append)
    assert ran == ["stocks"]
    assert out["stocks"]["compacted"] is True and "before_bytes" in out["stocks"]
    assert out["earnings"] == {"loose_bytes": 100, "compacted": False}


def test_a_failed_compaction_is_recorded_not_raised(tmp_path):
    _clone(tmp_path, "stocks", loose=900)

    def boom(name):
        raise RuntimeError("server busy")

    out = rdd.compact_loose(tmp_path, ["stocks"], threshold=500, gc=boom)
    assert out["stocks"]["compacted"] is False
    assert out["stocks"]["error"] == "RuntimeError: server busy"


def _bare_clone(tmp_path, name="earnings"):
    repo = tmp_path / name
    (repo / ".dolt").mkdir(parents=True)
    return repo


class _Refused(Exception):
    errno = 2003


def test_the_pull_goes_through_the_running_server(tmp_path, monkeypatch):
    """2026-10-09: the CLI pull opened a server-held clone read-only and failed; through the server
    it lands, and the CLI is never run while a server answers."""
    monkeypatch.setattr(rdd, "_pull_cli", lambda repo: (_ for _ in ()).throw(AssertionError("cli ran")))
    got = rdd._pull(_bare_clone(tmp_path), via_server=lambda name: f"{name}: merge successful")
    assert got == {"ok": True, "via": "server", "tail": ["earnings: merge successful"]}


def test_only_an_unreachable_server_falls_back_to_the_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(rdd, "_pull_cli", lambda repo: {"ok": True, "via": "cli"})

    def refused(name):
        raise _Refused("Can't connect to MySQL server on '127.0.0.1:3306'")

    assert rdd._pull(_bare_clone(tmp_path), via_server=refused) == {"ok": True, "via": "cli"}


def test_a_pull_the_server_refuses_is_recorded_not_retried_by_the_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(rdd, "_pull_cli", lambda repo: (_ for _ in ()).throw(AssertionError("cli ran")))

    def conflicted(name):
        raise RuntimeError("merge conflict in earnings_calendar")

    got = rdd._pull(_bare_clone(tmp_path), via_server=conflicted)
    assert got["ok"] is False and got["via"] == "server" and "merge conflict" in got["reason"]
