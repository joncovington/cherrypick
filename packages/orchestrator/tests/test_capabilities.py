"""Capabilities: what the machine has, ANDed with what someone switched on.

The rule these pin: a feature that needs `claude` or `dolt` is off on a machine that has not recorded
it, whatever its own switch says, and every surface sees the same answer because the AND lives in the
config resolvers. Absent means false, like `modules.<m>.enabled`.
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from cherrypick.orchestrator import capabilities as caps
from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import jobspec

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=ZoneInfo("America/New_York"))


def _cfg(**capabilities):
    cfg = {
        "modules": {
            "meic": {"enabled": True},
            "earnings": {"enabled": True, "paper": {"entry_time": "15:45"}},
            "pmcc": {"enabled": False},
        },
        "advisor": {"enabled": True},
        "review": {"narrative": True},
        "morning": {"narrative": True},
    }
    if capabilities:
        cfg["capabilities"] = capabilities
    return cfg


def test_absent_capabilities_switch_off_everything_that_needs_one():
    cfg = _cfg()
    assert set(cfgmod.enabled_modules(cfg)) == {"meic"}
    assert cfgmod.module_states(cfg)["earnings"] == {
        "configured": True,
        "enabled": False,
        "missing": ["dolt"],
    }
    assert cfgmod.advisor_settings(cfg)["enabled"] is False
    assert cfgmod.review_settings(cfg)["narrative"] is False
    assert cfgmod.morning_settings(cfg)["narrative"] is False
    assert cfgmod.technicals_settings(cfg)["enabled"] is False


def test_recorded_capabilities_let_each_feature_follow_its_own_switch():
    cfg = _cfg(claude=True, dolt=True)
    assert set(cfgmod.enabled_modules(cfg)) == {"meic", "earnings"}
    assert cfgmod.advisor_settings(cfg)["enabled"] is True
    assert cfgmod.review_settings(cfg)["narrative"] is True
    assert cfgmod.technicals_settings(cfg)["enabled"] is True
    # A capability never switches a feature on by itself.
    cfg["advisor"]["enabled"] = False
    assert cfgmod.advisor_settings(cfg)["enabled"] is False


def test_only_a_literal_true_counts():
    cfg = _cfg(claude="true", dolt=1)
    assert cfgmod.capabilities(cfg) == {"claude": False, "dolt": False}


def test_each_capability_gates_only_its_own_features():
    cfg = _cfg(claude=True)  # no dolt
    assert "earnings" not in cfgmod.enabled_modules(cfg)
    assert cfgmod.advisor_settings(cfg)["enabled"] is True
    cfg = _cfg(dolt=True)  # no claude
    assert "earnings" in cfgmod.enabled_modules(cfg)
    assert cfgmod.advisor_settings(cfg)["enabled"] is False


def test_the_scheduler_derives_no_dolt_or_ai_jobs_without_the_capabilities(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "STATE_DIR", tmp_path)

    def derive(cfg):
        jobs, errors = jobspec.derive_jobs(cfg, launcher="run.py", pythonw="pythonw", now=NOW)
        assert not errors, errors
        return {j.id: j for j in jobs}

    jobs = derive(_cfg())
    assert "earnings-paper" not in jobs and "earnings-entry" not in jobs
    for job_id in ("earnings-dolt-pull", "technicals-land", "review-narrative", "morning-narrative"):
        if job_id in jobs:
            assert jobs[job_id].enabled is False, job_id
    jobs = derive(_cfg(claude=True, dolt=True))
    assert jobs["earnings-dolt-pull"].enabled is True
    assert jobs["review-narrative"].enabled is True


def test_write_creates_the_block_then_splices_it_keeping_the_rest(tmp_path, monkeypatch):
    monkeypatch.setattr("cherrypick.orchestrator.configedit.backup_dir", lambda: tmp_path / "backups")
    path = tmp_path / "config.json"
    path.write_text('{\n  "_note": "kept",\n  "modules": {}\n}\n', encoding="utf-8")
    caps.write({"claude": True}, path=path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["capabilities"] == {"claude": True} and doc["_note"] == "kept"
    caps.write({"dolt": False}, path=path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["capabilities"] == {"claude": True, "dolt": False}  # an unnamed capability is kept
    assert list((tmp_path / "backups").iterdir())  # every write leaves the previous version


def test_parse_set_refuses_unknown_names_and_values():
    assert caps.parse_set(["claude=TRUE", "dolt=false"]) == {"claude": True, "dolt": False}
    with pytest.raises(ValueError):
        caps.parse_set(["gpu=true"])
    with pytest.raises(ValueError):
        caps.parse_set(["claude=yes"])


def test_detect_needs_the_dolt_clones_not_just_the_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(caps.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(caps, "_run_version", lambda argv: "v1")
    cfg = {"modules": {"earnings": {"paper": {"dolt_service": {"data_dir": str(tmp_path)}}}}}
    found = caps.detect(cfg)
    assert found["claude"]["present"] is True
    assert found["dolt"]["present"] is False and "earnings, options, stocks" in found["dolt"]["detail"]
    for db in caps.DOLT_DATABASES:
        (tmp_path / db / ".dolt").mkdir(parents=True)
    assert caps.detect(cfg)["dolt"]["present"] is True


def test_detect_reports_a_missing_cli_as_absent(monkeypatch):
    monkeypatch.setattr(caps.shutil, "which", lambda name: None)
    found = caps.detect({})
    assert found == {
        "claude": {"present": False, "detail": "claude not found on PATH"},
        "dolt": {"present": False, "detail": "dolt not found on PATH"},
    }


def test_gated_features_is_the_one_view_the_console_reads():
    view = caps.gated_features(_cfg(dolt=True))
    assert view["capabilities"] == {"claude": False, "dolt": True}
    assert view["modules"]["earnings"]["enabled"] is True
    assert view["modules"]["pmcc"] == {"configured": False, "enabled": False, "missing": []}
    assert view["features"]["advisor"] is False
    assert view["features"]["technicals"] is True
    assert view["features"]["options_flow"] is False  # off until a person signs in and switches it on
    cfg = _cfg(dolt=True)
    cfg["quikoptions"] = {"enabled": True}
    assert caps.gated_features(cfg)["features"]["options_flow"] is True


def test_cli_dispatches_capabilities(monkeypatch):
    from cherrypick import cli

    seen = {}
    monkeypatch.setattr(cli, "_emit", lambda obj: seen.setdefault("out", obj))
    cli.cmd_capabilities(_cfg(claude=True), SimpleNamespace(cap_set=None, detect=False, write=False))
    assert seen["out"]["capabilities"] == {"claude": True, "dolt": False}
