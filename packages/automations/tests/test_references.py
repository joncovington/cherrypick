"""Each automation's backtest reference (`references`, `cli reference-check`, `cli builder`): the pinned
Builder run per automation, kept in its own block so a pin moves nothing an automation trades."""

from __future__ import annotations

import copy
import io
import json

import pytest

from cherrypick.automations import arms, cli, enrolment, references
from cherrypick.automations import config as config_mod

RUN = "builder-iron-condor-1345-2611201000"


def test_a_pin_is_checked_and_the_block_to_write_returned_with_its_notes():
    cfg = {"references": {"_note": "kept", "long-put-1030": "builder-long-put-1030-2611010900"}}
    r = references.check(cfg, {"name": "iron-condor-1345", "run": RUN})
    assert r["problems"] == []
    assert r["block"] == {
        "_note": "kept",
        "long-put-1030": "builder-long-put-1030-2611010900",
        "iron-condor-1345": RUN,
    }


def test_a_new_pin_replaces_the_old_and_null_unpins():
    cfg = {"references": {"iron-condor-1345": "builder-iron-condor-1345-2601010900"}}
    assert references.check(cfg, {"name": "iron-condor-1345", "run": RUN})["block"] == {
        "iron-condor-1345": RUN
    }
    assert references.check(cfg, {"name": "iron-condor-1345", "run": None})["block"] == {}


@pytest.mark.parametrize(
    "req, why",
    [
        ({"name": "nope", "run": "builder-nope-2611201000"}, "no automation named nope"),
        (
            {"name": "iron-condor-1345", "run": "builder-long-put-1030-2611201000"},
            "not one of iron-condor-1345's",
        ),
        ({"name": "iron-condor-1345", "run": "my-hand-run"}, "not one of iron-condor-1345's"),
        ({"name": "iron-condor-1345", "run": "builder-iron-condor-1345-26112010"}, "not one of"),
    ],
)
def test_a_pin_to_another_automations_run_or_a_bad_name_is_refused(req, why):
    r = references.check({}, req)
    assert r["block"] is None
    assert any(why in p for p in r["problems"])


def test_a_bad_block_in_the_file_is_named_not_overwritten():
    r = references.check({"references": {"iron-condor-1345": 7}}, {"name": "iron-condor-1345", "run": RUN})
    assert r["block"] is None and r["problems"][0].startswith("references.iron-condor-1345")
    with pytest.raises(ValueError):
        references.block({"references": ["x"]})


def test_a_pin_moves_no_block_hash_and_no_backtest_label():
    before = config_mod.load_arms({})
    after = config_mod.load_arms({"references": {"iron-condor-1345": RUN}})
    assert enrolment.labels(after["iron-condor-1345"]) == enrolment.labels(before["iron-condor-1345"])
    assert after == before


def test_the_builder_lists_each_automations_pinned_run(tmp_path, capsys, managed_home):
    cfg = tmp_path / "automations.json"
    cfg.write_text(json.dumps({"references": {"iron-condor-1345": RUN}}), encoding="utf-8")
    assert cli.main(["--config", str(cfg), "builder"]) == 0
    rows = {r["name"]: r for r in json.loads(capsys.readouterr().out)["automations"]}
    assert rows["iron-condor-1345"]["reference"] == RUN
    assert rows["long-put-1030"]["reference"] is None


def test_the_check_verb_reads_stdin_and_writes_nothing(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "automations.json"
    text = json.dumps({"automations": {}})
    cfg.write_text(text, encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"name": "iron-condor-1345", "run": RUN})))
    assert cli.main(["--config", str(cfg), "reference-check"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["block"] == {"iron-condor-1345": RUN}
    assert cfg.read_text(encoding="utf-8") == text


def test_every_shipped_automation_name_fits_the_run_pattern():
    for name in copy.deepcopy(arms.ARMS):
        assert references.RUN.match(f"builder-{name}-2611201000").group("name") == name


def test_a_stale_pin_for_a_removed_automation_can_be_cleared():
    cfg = {"references": {"gone": "builder-gone-2611201000"}}
    assert references.check(cfg, {"name": "gone", "run": None})["block"] == {}
    assert references.check(cfg, {"name": "gone", "run": "builder-gone-2611211000"})["block"] is None
