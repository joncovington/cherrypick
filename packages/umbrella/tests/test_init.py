"""`cherrypick init` — config scaffold + structural validation."""

from __future__ import annotations

import json

from cherrypick.orchestrator import init


def _levels(issues):
    return {lvl for lvl, _ in issues}


def test_validate_missing_modules_is_error():
    issues = init.validate_config({})
    assert ("error", "'modules' section is missing or not an object") in issues


def test_validate_clean_config_has_no_errors():
    cfg = {
        "timezone": "America/New_York",
        "modules": {
            "meic": {"enabled": True, "repo": "https://x/cherrypick-meic.git",
                     "paper": {"trade_schema": "meic_ic"}},
        },
        "notify": {"channels": ["log", "desktop"]},
    }
    issues = init.validate_config(cfg)
    assert "error" not in _levels(issues)


def test_validate_module_without_repo_or_path_is_error():
    cfg = {"modules": {"meic": {"enabled": True, "paper": {"trade_schema": "meic_ic"}}}}
    assert ("error", "module 'meic' needs a 'repo' URL or a 'path'") in init.validate_config(cfg)


def test_validate_warns_on_no_enabled_modules_and_unknown_channel():
    cfg = {
        "timezone": "ET",
        "modules": {"meic": {"enabled": False, "path": "../x", "paper": {"trade_schema": "meic_ic"}}},
        "notify": {"channels": ["log", "carrier-pigeon"]},
    }
    issues = init.validate_config(cfg)
    assert "error" not in _levels(issues)
    msgs = " ".join(m for _, m in issues)
    assert "no modules are enabled" in msgs
    assert "carrier-pigeon" in msgs


def test_validate_warns_when_paper_schema_absent():
    cfg = {"modules": {"meic": {"enabled": True, "repo": "https://x.git"}}}
    issues = init.validate_config(cfg)
    assert any("no paper.trade_schema" in m for _, m in issues)


def test_scaffold_creates_when_absent_then_refuses_without_force(tmp_path):
    target = tmp_path / "config.json"
    r1 = init.scaffold(target=target)
    assert r1["created"] and target.exists()
    body = json.loads(target.read_text(encoding="utf-8"))
    assert "modules" in body

    # second call must NOT clobber
    r2 = init.scaffold(target=target)
    assert r2["created"] is False and "already exists" in r2["reason"]

    # force overwrites
    target.write_text("{}", encoding="utf-8")
    r3 = init.scaffold(target=target, force=True)
    assert r3["created"] and "modules" in json.loads(target.read_text(encoding="utf-8"))
