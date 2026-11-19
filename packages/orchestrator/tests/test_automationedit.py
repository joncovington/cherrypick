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
    # The file is written with the platform's line ending, as every config save is.
    text = cfgfile.read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert '"_header": "automations: kept as written"' in text
    assert '\n    "my-fly": ' in text  # the block's own indentation
    assert text.startswith(FILE.replace("\r\n", "\n").split('"long-put-1030"')[0])


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
    # The suite config path is fixed at import; never read the machine's own.
    monkeypatch.setattr(configcli.cfgmod, "load_config", lambda: {"modules": {}})
    monkeypatch.setattr(automationedit.draft, "__defaults__", (module(),))
    monkeypatch.setattr(automationedit.save, "__defaults__", (module(),))
    assert configcli.handle({"op": "automation_draft", "name": "my-fly"})["ok"] is True
    assert configcli.handle({"op": "automation_save", "name": "my-fly", "automation": SPEC})["added"] is True


def test_insert_key_takes_the_objects_indentation_and_line_ending():
    out = configedit.insert_key(FILE, "/automations", "my-fly", {"enabled": False})
    assert '"long-put-1030": {"enabled": true},\r\n    "my-fly": {"enabled": false}\r\n  }' in out
    lf = configedit.insert_key(FILE.replace("\r\n", "\n"), "/automations", "my-fly", 1)
    assert '"long-put-1030": {"enabled": true},\n    "my-fly": 1\n  }' in lf


def test_insert_key_into_an_empty_and_a_one_line_object():
    assert json.loads(configedit.insert_key('{"a": {}}', "/a", "k", 1)) == {"a": {"k": 1}}
    assert configedit.insert_key('{"a": {"x": 1}}', "/a", "k", 2) == '{"a": {"x": 1, "k": 2}}'
    with pytest.raises(ValueError):
        configedit.insert_key('{"a": {"k": 1}}', "/a", "k", 2)
    with pytest.raises(ValueError):
        configedit.insert_key('{"a": [1]}', "/a", "k", 2)


def test_today_passes_the_name_and_an_unsaved_edit_through_and_maps_failures():
    seen = []

    def run(_cfg, body):
        seen.append(body)
        return {"ok": True, "name": body["name"], "state": "would_skip"}

    assert automationedit.today({}, {"name": "mine"}, run=run)["state"] == "would_skip"
    automationedit.today({}, {"name": "mine", "automation": {"symbol": "SPX"}, "enabled": True}, run=run)
    assert seen == [{"name": "mine"}, {"name": "mine", "automation": {"symbol": "SPX"}}]
    assert automationedit.today({}, {}, run=run)["code"] == "bad_request"
    assert automationedit.today({}, {"name": "mine", "automation": []}, run=run)["code"] == "bad_request"
    assert automationedit.today({}, {"name": "mine"}, run=lambda c, b: None)["code"] == "unavailable"


def test_payoff_passes_the_name_and_an_unsaved_edit_through_and_maps_failures():
    seen = []

    def run(_cfg, body):
        seen.append(body)
        return {"ok": True, "name": body["name"], "state": "placed"}

    assert automationedit.payoff({}, {"name": "mine"}, run=run)["state"] == "placed"
    automationedit.payoff({}, {"name": "mine", "automation": {"symbol": "SPX"}, "start": "x"}, run=run)
    assert seen == [{"name": "mine"}, {"name": "mine", "automation": {"symbol": "SPX"}}]
    assert automationedit.payoff({}, {}, run=run)["error"] == "automation_payoff needs a 'name'"
    assert (
        automationedit.payoff({}, {"name": "mine"}, run=lambda c, b: {"ok": False})["code"] == "bad_request"
    )
    assert automationedit.payoff({}, {"name": "mine"}, run=lambda c, b: None)["code"] == "unavailable"


def test_configcli_routes_the_payoff_op_to_the_module(monkeypatch):
    from cherrypick.orchestrator import configcli

    monkeypatch.setattr(configcli.cfgmod, "load_config", lambda *a, **k: {})
    monkeypatch.setattr(
        automationedit, "run_module_payoff", lambda c, b: {"ok": True, "state": "placed", **b}
    )
    out = configcli.handle({"op": "automation_payoff", "name": "mine"})
    assert out == {"ok": True, "state": "placed", "name": "mine"}


OLDER = (
    "{\r\n"
    '  "automations": {\r\n'
    '    "mine": {"enabled": false, "legs": [\r\n'
    '      {"name": "short_put", "right": "P", "side": -1, "strike": {"rule": "delta"}}\r\n'
    "    ]},\r\n"
    '    "other": {"enabled": true}\r\n'
    "  }\r\n"
    "}\r\n"
)
WRITTEN = [{"name": "short_put", "action": "STO", "qty": 1, "side": "put", "strike": {"rule": "delta"}}]


@pytest.fixture
def olderfile(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    path = core_home.config_path("automations")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(OLDER.encode("utf-8"))
    return path


def answers(convert, problems=()):
    """A stand-in for the module's `cli legs-convert`."""

    def run(_cfg, _req):
        return {"ok": True, "convert": convert, "problems": list(problems)}

    return run


def test_converting_legs_is_a_dry_run_unless_applied_and_writes_only_the_legs(olderfile):
    run = answers({"mine": WRITTEN})
    before = olderfile.read_bytes()
    out = automationedit.convert_legs({}, run=run)
    assert out == {"ok": True, "applied": False, "convert": ["mine"]} and olderfile.read_bytes() == before
    out = automationedit.convert_legs({}, apply=True, run=run)
    assert out["ok"] is True and out["applied"] is True
    doc = json.loads(olderfile.read_text(encoding="utf-8"))
    assert doc["automations"]["mine"] == {"enabled": False, "legs": WRITTEN}
    assert doc["automations"]["other"] == {"enabled": True}


def test_converting_legs_refuses_what_the_module_says_would_change_and_a_leg_it_does_not_write(olderfile):
    before = olderfile.read_bytes()
    moved = answers({"mine": WRITTEN}, ["mine: converting would change how it loads"])
    assert automationedit.convert_legs({}, apply=True, run=moved)["code"] == "invalid"
    older = answers({"mine": [{**WRITTEN[0], "right": "P"}]})
    assert automationedit.convert_legs({}, apply=True, run=older)["code"] == "invalid"
    assert automationedit.convert_legs({}, apply=True, run=lambda c, r: None)["code"] == "unavailable"
    assert olderfile.read_bytes() == before


def test_strategy_passes_the_name_and_settings_through_and_maps_failures():
    seen = []

    def run(_cfg, body):
        seen.append(body)
        return {"ok": True, "strategy": body["strategy"], "legs": [], "problems": []}

    assert automationedit.strategy({}, {"strategy": "vertical", "settings": {"width": 5}, "x": 1}, run=run)[
        "ok"
    ]
    assert seen == [{"strategy": "vertical", "settings": {"width": 5}}]
    assert automationedit.strategy({}, {}, run=run)["code"] == "bad_request"
    assert automationedit.strategy({}, {"strategy": "v", "settings": []}, run=run)["code"] == "bad_request"
    assert automationedit.strategy({}, {"strategy": "v"}, run=lambda c, b: None)["code"] == "unavailable"


def test_configcli_routes_the_strategy_op_to_the_module(monkeypatch):
    monkeypatch.setattr(configcli.cfgmod, "load_config", lambda *a, **k: {})
    monkeypatch.setattr(automationedit, "run_module_strategy", lambda c, b: {"ok": True, **b})
    assert configcli.handle({"op": "automation_strategy", "strategy": "vertical"}) == {
        "ok": True,
        "strategy": "vertical",
        "settings": {},
    }
