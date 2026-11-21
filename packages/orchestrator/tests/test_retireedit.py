"""An automation's page retires or reinstates it (`retireedit`, configcli `automation_retire`): the module's
check, then only `retired` and `enabled` in that automation's entry, in one config save."""

from __future__ import annotations

import json

import pytest
from cherrypick.core import home as core_home

from cherrypick.orchestrator import retireedit

FILE = (
    "{\r\n"
    '  "_header": "automations: kept as written",\r\n'
    '  "groups": {"ensemble": {"enabled": false}},\r\n'
    '  "automations": {"iron-fly-1000": {"enabled": true, "note": "kept"}}\r\n'
    "}\r\n"
)


@pytest.fixture
def cfgfile(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    path = core_home.config_path("automations")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(FILE.encode("utf-8"))
    return path


def module(entry=None, problems=(), seen=None):
    def run(cfg, req):
        if seen is not None:
            seen.append(req)
        if problems:
            return {"ok": True, "problems": list(problems)}
        default = {"retired": "2026-11-21" if req["retired"] else False, "enabled": False}
        return {"ok": True, "problems": [], "entry": default if entry is None else entry, "notes": ["n"]}

    return run


def doc(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_retiring_sets_the_two_keys_and_leaves_the_rest_as_written(cfgfile):
    seen: list = []
    out = retireedit.save({}, {"name": "iron-fly-1000", "retired": True}, module(seen=seen))
    assert out["ok"] is True and out["retired"] == "2026-11-21"
    assert seen == [{"name": "iron-fly-1000", "retired": True}]
    d = doc(cfgfile)
    assert d["automations"]["iron-fly-1000"] == {"enabled": False, "note": "kept", "retired": "2026-11-21"}
    assert d["_header"] == "automations: kept as written" and d["groups"] == {"ensemble": {"enabled": False}}


def test_a_shipped_automation_the_file_does_not_name_gets_an_entry(cfgfile):
    out = retireedit.save({}, {"name": "orb-put-spread", "retired": True}, module())
    assert out["ok"] is True
    assert doc(cfgfile)["automations"]["orb-put-spread"] == {"retired": "2026-11-21", "enabled": False}


def test_reinstating_writes_false_and_never_switches_it_on(cfgfile):
    retireedit.save({}, {"name": "iron-fly-1000", "retired": True}, module())
    out = retireedit.save({}, {"name": "iron-fly-1000", "retired": False}, module())
    assert out["ok"] is True
    assert doc(cfgfile)["automations"]["iron-fly-1000"]["retired"] is False
    assert doc(cfgfile)["automations"]["iron-fly-1000"]["enabled"] is False


def test_a_problem_from_the_module_writes_nothing(cfgfile):
    out = retireedit.save({}, {"name": "iron-fly-1000", "retired": True}, module(problems=["retired"]))
    assert out["ok"] is False and out["code"] == "invalid"
    assert cfgfile.read_bytes() == FILE.encode("utf-8")


@pytest.mark.parametrize(
    "entry",
    [
        {"retired": "2026-11-21", "enabled": True},  # would switch one on
        {"retired": "2026-11-21", "enabled": False, "group": None},  # a key it does not take
        {"retired": False, "enabled": False},  # the other way round for a retire
    ],
)
def test_an_entry_this_writer_does_not_take_is_refused(cfgfile, entry):
    out = retireedit.save({}, {"name": "iron-fly-1000", "retired": True}, module(entry=entry))
    assert out["ok"] is False and out["code"] == "invalid"
    assert cfgfile.read_bytes() == FILE.encode("utf-8")


def test_a_request_without_a_name_or_a_boolean_is_refused_before_the_module_is_asked(cfgfile):
    seen: list = []
    assert retireedit.save({}, {"name": "iron-fly-1000", "retired": "yes"}, module(seen=seen))["ok"] is False
    assert retireedit.save({}, {"retired": True}, module(seen=seen))["ok"] is False
    assert seen == []
