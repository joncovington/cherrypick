"""`cli builder`: every automation block by block for the console's Builder page, each block's keys and
backtest label, the start and forward rule, and the backtest suggestion for one not wholly backtested."""

from __future__ import annotations

import copy
import json

from cherrypick.automations import arms, cli, enrolment

A = arms.load_arms()


def test_the_builder_view_holds_every_block_with_its_keys_and_label():
    arm = copy.deepcopy(A["iron-condor-1345"])
    arm["exit"] = {**arm["exit"], "close_at_dte": 1}
    view = enrolment.builder_view(arm, None)
    assert [b["block"] for b in view["blocks"]] == list(enrolment.BLOCKS)
    by = {b["block"]: b for b in view["blocks"]}
    assert by["exit"]["state"] == "changed" and by["exit"]["keys"]["exit"]["close_at_dte"] == 1
    assert by["structure"]["keys"] == {k: arm[k] for k in ("symbol", "expiry", "legs")}
    assert view["register"] == {"item": 75, "verdict": "passed"} and view["suggestion"] is None
    assert view["summary"] == enrolment.summary(arm)


def test_the_cli_prints_every_automation_and_suggests_for_a_new_one(tmp_path, capsys, managed_home):
    new = copy.deepcopy(A["long-put-1030"])
    new.pop("register")
    new.pop("group")
    new.update(name="mine", start="2026-11-18")
    cfg = tmp_path / "automations.json"
    cfg.write_text(json.dumps({"automations": {"mine": new}}), encoding="utf-8")
    assert cli.main(["--config", str(cfg), "builder"]) == 0
    out = json.loads(capsys.readouterr().out)
    rows = {r["name"]: r for r in out["automations"]}
    assert set(rows) == set(A) | {"mine"}
    assert rows["mine"]["start"] == "2026-11-18" and rows["mine"]["suggestion"]["possible"] is False
    assert rows["iron-condor-1345"]["suggestion"] is None
    assert out["blocks"] == list(enrolment.BLOCKS)
