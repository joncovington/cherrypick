"""A config.json that cannot be parsed (item 7 of the 2026-10-08 audit): every job, the watchdog and
every notifier load it fresh and used to crash -- silencing the suite, warnings included."""

from __future__ import annotations

import json

import pytest

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import watchdog as wd

GOOD = {"modules": {"flies": {"enabled": True}}, "notify": {"channels": ["log", "discord"]}}


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", path)
    monkeypatch.setattr(cfgmod, "LEGACY_CONFIG_PATH", tmp_path / "absent.json")
    return path


def test_a_good_load_keeps_a_copy_and_a_broken_one_runs_on_it(cfg_file):
    cfg_file.write_text(json.dumps(GOOD), encoding="utf-8")
    assert cfgmod.load_config() == GOOD
    assert json.loads(cfgmod.last_good_path().read_text(encoding="utf-8")) == GOOD
    cfg_file.write_text('{"modules": {"flies": {"enabled": true},}', encoding="utf-8")  # a stray comma
    assert cfgmod.load_config() == GOOD
    (f,) = wd._check_config_health()
    assert f.key == "config.unreadable" and f.status == wd.CRITICAL and "JSONDecodeError" in f.message


def test_the_first_good_read_clears_the_warning(cfg_file):
    cfg_file.write_text(json.dumps(GOOD), encoding="utf-8")
    cfgmod.load_config()
    cfg_file.write_text("{", encoding="utf-8")
    cfgmod.load_config()
    assert wd._check_config_health()
    cfg_file.write_text(json.dumps({**GOOD, "x": 1}), encoding="utf-8")
    assert cfgmod.load_config()["x"] == 1 and wd._check_config_health() == []


def test_no_good_copy_still_fails_loudly_and_an_explicit_path_never_falls_back(cfg_file, tmp_path):
    cfg_file.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        cfgmod.load_config()
    cfg_file.write_text(json.dumps(GOOD), encoding="utf-8")
    cfgmod.load_config()
    other = tmp_path / "mine.json"
    other.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        cfgmod.load_config(other)
