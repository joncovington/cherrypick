"""The shared command-line arm: the confirmation, the disclaimer and the module-off refusal."""

from __future__ import annotations

import json

import pytest

from cherrypick.core import home as _home
from cherrypick.core import live


@pytest.fixture(autouse=True)
def _home_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    return tmp_path


def _suite(modules):
    path = _home.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"modules": modules}), encoding="utf-8")


def _arm(**kw):
    shown, installed = [], []
    kw.setdefault("confirm", None)
    kw.setdefault("prompted", True)
    kw.setdefault("require_enabled", True)
    out = live.arm_from_cli("flies", lambda: installed.append(1) or {"ok": True}, show=shown.append, **kw)
    return out, shown, installed


def test_the_switch_is_false_for_a_missing_malformed_or_absent_module():
    assert live.suite_module_enabled("flies") is False
    _home.config_path().write_text("{not json", encoding="utf-8")
    assert live.suite_module_enabled("flies") is False
    _suite({"bwb": {"enabled": True}})
    assert live.suite_module_enabled("flies") is False
    _suite({"flies": {"enabled": True}})
    assert live.suite_module_enabled("flies") is True


def test_the_disclaimer_is_the_repo_file_verbatim():
    text = live.disclaimer_text()
    assert text is not None and text.startswith("# Disclaimer")


def test_a_module_that_is_off_refuses_before_showing_anything():
    _suite({"flies": {"enabled": False}})
    out, shown, installed = _arm(confirm="YES")
    assert out["ok"] is False and "retired and switched off" in out["error"]
    assert not shown and not installed


def test_without_the_exact_word_it_shows_the_disclaimer_and_installs_nothing():
    _suite({"flies": {"enabled": True}})
    for word in (None, "yes", "YES ", "Y", ""):
        out, shown, installed = _arm(confirm=word)
        assert out["ok"] is False and "Nothing was armed" in out["error"]
        assert shown == [live.disclaimer_text()] and not installed


def test_the_exact_word_installs():
    _suite({"flies": {"enabled": True}})
    out, shown, installed = _arm(confirm="YES")
    assert out == {"ok": True} and installed == [1] and shown == [live.disclaimer_text()]


def test_the_agent_path_installs_without_a_prompt_but_not_while_off():
    _suite({"flies": {"enabled": True}})
    out, shown, installed = _arm(prompted=False)
    assert out["ok"] is True and installed == [1] and not shown
    _suite({"flies": {"enabled": False}})
    out, shown, installed = _arm(prompted=False)
    assert out["ok"] is False and not installed


def test_a_live_module_with_no_switch_requirement_ignores_the_suite_config():
    out, shown, installed = _arm(confirm="YES", require_enabled=False)
    assert out["ok"] is True and installed == [1]
