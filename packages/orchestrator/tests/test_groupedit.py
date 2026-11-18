"""The Groups page save (`groupedit`, configcli `automation_groups*`): the module's check first, then the
groups block and each moved automation's `group` written in one config save, the rest untouched."""

from __future__ import annotations

import json

import pytest
from cherrypick.core import home as core_home

from cherrypick.orchestrator import configcli, groupedit

FILE = (
    "{\r\n"
    '  "_header": "automations: kept as written",\r\n'
    '  "groups": {"ensemble": {"enabled": false}},\r\n'
    '  "automations": {\r\n'
    '    "iron-fly-1000": {"enabled": true},\r\n'
    '    "long-put-1030": {"enabled": true, "group": "ensemble"}\r\n'
    "  }\r\n"
    "}\r\n"
)
BLOCK = {"ensemble": {"enabled": True, "max_risk": 2000}, "gold": {"enabled": False}}


@pytest.fixture
def cfgfile(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    path = core_home.config_path("automations")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(FILE.encode("utf-8"))
    return path


def module(problems=(), block=BLOCK, moves=None, seen=None):
    def run(cfg, req):
        if seen is not None:
            seen.append(req)
        if problems:
            return {"ok": True, "problems": list(problems), "block": None, "moves": {}}
        return {"ok": True, "problems": [], "block": block, "moves": moves or {}}

    return run


def doc(path):
    return json.loads(path.read_text(encoding="utf-8"))


REQ = {"groups": BLOCK, "members": {}}


def test_the_block_is_replaced_and_each_move_written_where_it_belongs(cfgfile):
    moves = {"iron-fly-1000": "gold", "long-put-1030": None, "gld-call-spread-1dte": "gold"}
    out = groupedit.save({}, REQ, module(moves=moves))
    assert out["ok"] is True and out["moved"] == sorted(moves) and out["groups"] == ["ensemble", "gold"]
    d = doc(cfgfile)
    assert d["groups"] == BLOCK
    # Added to an entry without one, set in one that had it, and a one-key entry for a shipped automation.
    assert d["automations"]["iron-fly-1000"] == {"enabled": True, "group": "gold"}
    assert d["automations"]["long-put-1030"] == {"enabled": True, "group": None}
    assert d["automations"]["gld-call-spread-1dte"] == {"group": "gold"}
    assert d["_header"] == "automations: kept as written"


def test_a_problem_the_module_names_writes_nothing(cfgfile):
    before = cfgfile.read_bytes()
    out = groupedit.save(
        {}, REQ, module(problems=["groups.ensemble.shares: 120 percent in all, more than 100"])
    )
    assert out["ok"] is False and out["code"] == "invalid" and "more than 100" in out["error"]
    assert cfgfile.read_bytes() == before


def test_nothing_is_written_unless_the_module_returns_a_block(cfgfile):
    before = cfgfile.read_bytes()
    out = groupedit.save({}, REQ, module(block=None))
    assert out["ok"] is False and cfgfile.read_bytes() == before


def test_a_file_without_a_groups_block_gets_one(cfgfile):
    cfgfile.write_text('{"automations": {"iron-fly-1000": {"enabled": true}}}', encoding="utf-8")
    out = groupedit.save({}, REQ, module())
    assert out["ok"] is True and doc(cfgfile)["groups"] == BLOCK


def test_the_request_is_passed_to_the_module_as_sent(cfgfile):
    seen: list = []
    groupedit.check(
        {}, {"groups": BLOCK, "members": {"iron-fly-1000": "gold"}, "extra": 1}, module(seen=seen)
    )
    assert seen == [{"groups": BLOCK, "members": {"iron-fly-1000": "gold"}}]
    assert groupedit.check({}, {"members": {}}, module())["ok"] is False


def test_a_stale_page_is_refused(cfgfile):
    out = groupedit.save({}, {**REQ, "expected_mtime": 1}, module())
    assert out["ok"] is False and out["code"] == "conflict"


def test_the_module_unavailable_is_said(cfgfile):
    out = groupedit.view({}, {}, lambda cfg, req: None)
    assert out == {
        "ok": False,
        "error": "the automations module could not be asked (is it installed?)",
        "code": "unavailable",
    }


def test_configcli_routes_the_three_ops(cfgfile, monkeypatch):
    # The suite's own config is not this test's: CI has none.
    monkeypatch.setattr(configcli.cfgmod, "load_config", lambda: {"modules": {}})
    monkeypatch.setattr(
        groupedit, "run_module_view", lambda cfg, req: {"ok": True, "groups": [], "ungrouped": []}
    )
    monkeypatch.setattr(groupedit, "run_module_check", module())
    assert configcli.handle({"op": "automation_groups"}) == {"ok": True, "groups": [], "ungrouped": []}
    assert configcli.handle({"op": "automation_groups_check", **REQ})["problems"] == []
    assert configcli.handle({"op": "automation_groups_save", **REQ})["ok"] is True
    assert doc(cfgfile)["groups"] == BLOCK
