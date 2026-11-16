"""The Builder's save (`automationedit`, configcli `automation_draft` / `automation_save`): the module's
check first, the one entry it returned written off, and the rest of the file untouched."""

from __future__ import annotations

import json

import pytest
from cherrypick.core import home as core_home

from cherrypick.orchestrator import automationedit, configcli, configedit

FILE = (
    "{\r\n"
    '  "_header": "automations: kept as written",\r\n'
    '  "groups": {"ensemble": {"enabled": false}},\r\n'
    '  "automations": {\r\n'
    '    "iron-fly-1000": {"enabled": true},\r\n'
    '    "long-put-1030": {"enabled": true}\r\n'
    "  }\r\n"
    "}\r\n"
)
SPEC = {"symbol": "SPX", "legs": [], "enabled": True}


@pytest.fixture
def cfgfile(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    path = core_home.config_path("automations")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(FILE.encode("utf-8"))
    return path


def module(problems=(), enabled=False, seen=None):
    def run(cfg, req):
        if seen is not None:
            seen.append(req)
        if "automation" not in req:
            return {"ok": True, "name": req["name"], "automation": {"name": req["name"], **SPEC}}
        if problems:
            return {"ok": True, "name": req["name"], "problems": list(problems)}
        entry = {k: v for k, v in req["automation"].items() if k != "name"}
        return {"ok": True, "name": req["name"], "problems": [], "entry": {**entry, "enabled": enabled},
                "view": {"name": req["name"]}}  # fmt: skip

    return run


def doc(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_new_automation_is_added_last_and_off(cfgfile):
    out = automationedit.save({}, {"name": "my-fly", "automation": SPEC}, module())
    assert out["ok"] is True and out["added"] is True and out["enabled"] is False
    d = doc(cfgfile)
    assert list(d["automations"]) == ["iron-fly-1000", "long-put-1030", "my-fly"]
    assert d["automations"]["my-fly"]["enabled"] is False
    text = cfgfile.read_bytes().decode("utf-8")
    assert '"_header": "automations: kept as written"' in text
    assert '\r\n    "my-fly": ' in text  # the block's own indentation and line ending
    assert text.startswith(FILE.split('"long-put-1030"')[0])


def test_an_existing_automation_is_replaced_in_place(cfgfile):
    out = automationedit.save({}, {"name": "iron-fly-1000", "automation": SPEC}, module())
    assert out["ok"] is True and out["added"] is False
    d = doc(cfgfile)
    assert list(d["automations"]) == ["iron-fly-1000", "long-put-1030"]
    assert d["automations"]["iron-fly-1000"] == {"symbol": "SPX", "legs": [], "enabled": False}
    assert d["automations"]["long-put-1030"] == {"enabled": True}


def test_a_problem_the_module_names_writes_nothing(cfgfile):
    before = cfgfile.read_bytes()
    out = automationedit.save(
        {}, {"name": "my-fly", "automation": SPEC}, module(problems=["my-fly: missing 'legs'"])
    )
    assert out["ok"] is False and out["code"] == "invalid" and out["problems"] == ["my-fly: missing 'legs'"]
    assert cfgfile.read_bytes() == before


def test_an_entry_that_is_not_off_is_never_written(cfgfile):
    before = cfgfile.read_bytes()
    out = automationedit.save({}, {"name": "my-fly", "automation": SPEC}, module(enabled=True))
    assert out["ok"] is False and "off" in out["error"]
    assert cfgfile.read_bytes() == before


def test_a_module_that_cannot_be_asked_is_unavailable(cfgfile):
    out = automationedit.save({}, {"name": "my-fly", "automation": SPEC}, lambda cfg, req: None)
    assert out["ok"] is False and out["code"] == "unavailable"


def test_a_stale_mtime_is_a_conflict(cfgfile):
    out = automationedit.save({}, {"name": "my-fly", "automation": SPEC, "expected_mtime": 1}, module())
    assert out["ok"] is False and out["code"] == "conflict"


def test_the_older_block_spelling_is_written_where_it_is(cfgfile):
    cfgfile.write_text(json.dumps({"arms": {"iron-fly-1000": {"enabled": True}}}), encoding="utf-8")
    assert automationedit.save({}, {"name": "my-fly", "automation": SPEC}, module())["ok"] is True
    assert set(doc(cfgfile)["arms"]) == {"iron-fly-1000", "my-fly"} and "automations" not in doc(cfgfile)


def test_draft_passes_the_spec_and_the_check_through(cfgfile):
    seen = []
    assert (
        automationedit.draft({}, {"name": "iron-fly-1000"}, module(seen=seen))["automation"]["symbol"]
        == "SPX"
    )
    assert seen == [{"name": "iron-fly-1000"}]
    assert automationedit.draft({}, {"name": ""}, module())["code"] == "bad_request"
    assert automationedit.draft({}, {"name": "x", "automation": []}, module())["code"] == "bad_request"


def test_configcli_dispatches_both_ops(cfgfile, monkeypatch):
    (cfgfile.parent.parent / "config.json").write_text(json.dumps({"modules": {}}), encoding="utf-8")
    monkeypatch.setattr(automationedit.draft, "__defaults__", (module(),))
    monkeypatch.setattr(automationedit.save, "__defaults__", (module(),))
    assert configcli.handle({"op": "automation_draft", "name": "my-fly"})["ok"] is True
    assert configcli.handle({"op": "automation_save", "name": "my-fly", "automation": SPEC})["added"] is True


def test_insert_key_into_an_empty_and_a_one_line_object():
    assert json.loads(configedit.insert_key('{"a": {}}', "/a", "k", 1)) == {"a": {"k": 1}}
    assert configedit.insert_key('{"a": {"x": 1}}', "/a", "k", 2) == '{"a": {"x": 1, "k": 2}}'
    with pytest.raises(ValueError):
        configedit.insert_key('{"a": {"k": 1}}', "/a", "k", 2)
    with pytest.raises(ValueError):
        configedit.insert_key('{"a": [1]}', "/a", "k", 2)
