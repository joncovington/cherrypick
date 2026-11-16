"""The Builder's check before a save (`drafts`, `cli draft`): the loop's own rule, the entry always off,
and a register label never taken from the editor."""

from __future__ import annotations

import io
import json
from datetime import date

from cherrypick.automations import arms, cli, drafts

TODAY = date(2026, 11, 16)
CONFIG = {"automations": {"iron-fly-1000": {"enabled": True}}}


def by_block(view):
    return {b["block"]: b["state"] for b in view["blocks"]}


def test_the_editor_starts_from_the_shipped_entry_with_this_machines_changes():
    spec = drafts.editable(CONFIG, "iron-fly-1000")
    assert spec["name"] == "iron-fly-1000" and spec["enabled"] is True
    assert spec["legs"] == arms.ARMS["iron-fly-1000"]["legs"]
    assert drafts.editable(CONFIG, "nothing-here") is None


def test_an_edited_automation_is_checked_and_saved_off_with_its_register_kept():
    spec = drafts.editable(CONFIG, "iron-fly-1000")
    spec["entry"] = {**spec["entry"], "at": "10:30"}
    spec["enabled"] = True
    spec["register"] = {"item": 99, "verdict": "passed"}
    out = drafts.draft(CONFIG, "iron-fly-1000", spec, TODAY)
    assert out["problems"] == [] and out["new"] is False
    assert out["entry"]["enabled"] is False and "name" not in out["entry"]
    assert out["entry"]["register"] == arms.ARMS["iron-fly-1000"]["register"]
    states = by_block(out["view"])
    assert states["entry"] == "changed" and states["structure"] == "backtested"
    assert out["view"]["enabled"] is False


def test_a_copy_under_a_new_name_is_not_backtested():
    spec = drafts.editable(CONFIG, "iron-fly-1000")
    out = drafts.draft(CONFIG, "my-fly", spec, TODAY)
    assert out["problems"] == [] and out["new"] is True
    assert "register" not in out["entry"]
    assert set(by_block(out["view"]).values()) == {"not_backtested"}


def test_a_problem_the_loop_would_refuse_is_named():
    spec = drafts.editable(CONFIG, "iron-fly-1000")
    spec["entry"] = {"kind": "sometime"}
    spec["colour"] = "red"
    out = drafts.draft(CONFIG, "my-fly", spec, TODAY)
    assert "entry" not in out and out["problems"]
    assert any("my-fly: unknown key 'colour'" in p for p in out["problems"])
    assert all(p.startswith("my-fly") for p in out["problems"])


def test_a_bad_name_or_a_non_object_is_refused():
    assert drafts.draft(CONFIG, "My Fly", {}, TODAY)["problems"][0].startswith("name:")
    assert drafts.draft(CONFIG, "x", {}, TODAY)["problems"][0].startswith("name:")
    assert drafts.draft(CONFIG, "my-fly", [], TODAY)["problems"] == ["automation: an object"]


def test_the_cli_answers_on_stdout(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "automations.json"
    cfg.write_text(json.dumps(CONFIG), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"name": "iron-fly-1000"})))
    assert cli.main(["--config", str(cfg), "draft"]) == 0
    spec = json.loads(capsys.readouterr().out)["automation"]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"name": "new-one", "automation": spec})))
    assert cli.main(["--config", str(cfg), "draft"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["problems"] == [] and out["entry"]["enabled"] is False
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert cli.main(["--config", str(cfg), "draft"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is False
