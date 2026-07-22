"""doctor's Dolt health classification: reachability alone is not health when required databases
are declared. Guards the 2026-07-11 regression where a Dolt server rooted at the wrong data dir
answered on the port while serving none of the earnings databases, and a port-only check stayed green."""

import os
from types import SimpleNamespace

from cherrypick.orchestrator import doctor
from cherrypick.orchestrator.doctor import FAIL, OK, WARN


def _producer_cfg(streamer_dir):
    return {
        "timezone": "America/New_York",
        "modules": {},
        "notify": {"channels": ["log"]},
        "streamer": {"enabled": True, "path": str(streamer_dir), "status_argv": ["run.py", "--status"],
                     "stale_restart_seconds": 240},
    }


def _check(checks, name):
    return next((c for c in checks if c.name == name), None)


def _fake_status(stdout):
    """A doctor._run replacement returning a fixed streamer --status payload."""
    return lambda *a, **k: SimpleNamespace(returncode=0, stdout=stdout)


def test_module_path_check_is_portable(tmp_path, monkeypatch):
    """The `<module>.path` check must not leak the absolute checkout path (drive/username) when the
    module exists — portable paths only, same guardrail as the dashboard modules table."""
    monkeypatch.setattr(doctor.cfgmod, "ROOT", tmp_path)  # so a portable path resolves cleanly here
    module = tmp_path / "meic"
    module.mkdir()
    cfg = {
        "timezone": "America/New_York",
        "modules": {"meic": {"enabled": True, "path": str(module),
                             "paper": {"paper_db": "data/paper.db"}}},
        "notify": {"channels": ["log"]},
    }
    c = _check(doctor.run(cfg, fast=True), "meic.path")
    assert c is not None and c.status == OK
    assert not os.path.isabs(c.detail) and str(tmp_path) not in c.detail


def test_streamer_producer_running_shows_freshness(tmp_path, monkeypatch):
    (tmp_path / "streamer").mkdir()
    monkeypatch.setattr(doctor, "_run", _fake_status('{"running": true, "oldest_event_age_s": 3}'))
    c = _check(doctor.run(_producer_cfg(tmp_path / "streamer")), "streamer")
    assert c is not None and c.status == OK and "last event 3s ago" in c.detail


def test_streamer_producer_not_running_warns(tmp_path, monkeypatch):
    (tmp_path / "streamer").mkdir()
    monkeypatch.setattr(doctor, "_run", _fake_status('{"running": false}'))
    c = _check(doctor.run(_producer_cfg(tmp_path / "streamer")), "streamer")
    assert c is not None and c.status == WARN and "not running" in c.detail


def test_streamer_producer_silent_in_market_hours_warns(tmp_path, monkeypatch):
    (tmp_path / "streamer").mkdir()
    monkeypatch.setattr(doctor, "_run", _fake_status('{"running": true, "oldest_event_age_s": 900}'))
    monkeypatch.setattr(doctor.timeutil, "is_market_hours", lambda *a, **k: True)
    c = _check(doctor.run(_producer_cfg(tmp_path / "streamer")), "streamer")
    assert c is not None and c.status == WARN and "silent" in c.detail


def test_streamer_producer_silent_off_hours_stays_ok(tmp_path, monkeypatch):
    (tmp_path / "streamer").mkdir()
    monkeypatch.setattr(doctor, "_run", _fake_status('{"running": true, "oldest_event_age_s": 900}'))
    monkeypatch.setattr(doctor.timeutil, "is_market_hours", lambda *a, **k: False)
    c = _check(doctor.run(_producer_cfg(tmp_path / "streamer")), "streamer")
    assert c is not None and c.status == OK and "quiet off-hours" in c.detail


def test_find_stray_artifacts_empty_tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1")
    assert doctor.find_stray_artifacts([tmp_path]) == []


def test_find_stray_artifacts_flags_runtime_outputs(tmp_path):
    (tmp_path / "paper_trades.db").write_text("")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "watchdog.log").write_text("")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "watchdog.last.json").write_text("{}")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "strategy_dashboard.html").write_text("<html>")
    (tmp_path / "dashboard.html").write_text("<html>")
    found = {p.name for p in doctor.find_stray_artifacts([tmp_path])}
    assert found == {"paper_trades.db", "watchdog.log", "watchdog.last.json",
                     "strategy_dashboard.html", "dashboard.html"}


def test_find_stray_artifacts_skips_vendored_and_cache_dirs(tmp_path):
    for skip in ("_core", "__pycache__", ".git"):
        d = tmp_path / skip
        d.mkdir()
        (d / "leftover.db").write_text("")  # a db inside a vendored/cache dir is not a leak
    (tmp_path / "config.example.json").write_text("{}")  # non-runtime file, ignored
    assert doctor.find_stray_artifacts([tmp_path]) == []


def test_find_stray_artifacts_missing_root_is_safe(tmp_path):
    assert doctor.find_stray_artifacts([tmp_path / "does-not-exist"]) == []


def test_unreachable_is_warn():
    status, detail = doctor._dolt_status(reachable=False, required=["earnings"], present=None)
    assert status == WARN
    assert "not reachable" in detail


def test_reachable_no_required_dbs_is_ok():
    # Backward compatible: no dolt_databases declared -> reachability is enough.
    status, detail = doctor._dolt_status(reachable=True, required=[], present=None)
    assert status == OK
    assert detail == "reachable"


def test_reachable_but_no_client_skips_db_check():
    status, detail = doctor._dolt_status(reachable=True, required=["earnings"], present=None)
    assert status == OK
    assert "skipped" in detail


def test_all_required_present_is_ok():
    status, detail = doctor._dolt_status(
        reachable=True,
        required=["earnings", "options", "stocks"],
        present={"earnings", "options", "stocks", "information_schema", "mysql"},
    )
    assert status == OK
    assert "present" in detail


def test_missing_database_is_fail():
    # The exact 2026-07-11 case: server up, but the earnings DB isn't served.
    status, detail = doctor._dolt_status(
        reachable=True,
        required=["earnings", "options", "stocks"],
        present={"information_schema", "mysql"},
    )
    assert status == FAIL
    assert "earnings" in detail and "options" in detail and "stocks" in detail


def test_fast_mode_skips_broker_check(tmp_path, monkeypatch):
    """fast=True must not emit a broker/keyring check nor make the authenticated broker round-trip
    (`_run`) — it's the one call unsafe to poll on the live-checks cadence. The cheap local checks
    (interpreter, clock, module path/config, task registration, notify) still run."""
    module = tmp_path / "meic"
    module.mkdir()
    (module / "config.json").write_text("{}", encoding="utf-8")

    def fail_if_called(*a, **k):
        raise AssertionError("_run (broker/streamer subprocess) must not be invoked in fast mode")

    monkeypatch.setattr(doctor, "_run", fail_if_called)
    cfg = {
        "timezone": "America/New_York",
        "modules": {
            "meic": {
                "enabled": True,
                "path": str(module),
                "paper": {"paper_db": "data/paper.db", "task_name": "cherrypick-meic-paper-loop"},
                # no "streamer" block -> the only other _run caller is skipped too
            }
        },
        "notify": {"channels": ["log"]},
    }
    names = {c.name for c in doctor.run(cfg, fast=True)}
    assert "broker/keyring" not in names
    assert "python" in names and "meic.path" in names  # local checks still ran
    # Force the optional import to fail -> graceful None, never raises.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("mysql"):
            raise ImportError("no mysql client")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert doctor._dolt_databases("127.0.0.1", 3306) is None
