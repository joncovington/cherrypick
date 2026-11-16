"""A module renamed keeps working from a config written before the rename: `modules.ensemble` reads as
`modules.automations` with its old default path, argv and log spelt anew, and a study stamped before the
folder moved is not stale for the move alone."""

from __future__ import annotations

import hashlib
import json

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import studyversion as sv

OLD_BLOCK = {
    "enabled": True,
    "path": "../ensemble",
    "paper": {
        "status_argv": ["-m", "cherrypick.ensemble.paper_loop", "--status"],
        "once_argv": ["-m", "cherrypick.ensemble.paper_loop", "--once"],
        "paper_db": "~/.cherrypick/data/ensemble/paper_trades.db",
        "trade_schema": "ensemble_trade",
        "log": "ensemble_paper.log",
    },
}


def test_the_old_module_key_reads_as_the_new_one_in_its_place():
    cfg = {"modules": {"meic": {"enabled": True}, "ensemble": OLD_BLOCK, "ladder": {"enabled": False}}}
    out = cfgmod.resolve_renamed(cfg)
    assert list(out["modules"]) == ["meic", "automations", "ladder"]
    m = out["modules"]["automations"]
    assert m["path"] == "../automations" and m["enabled"] is True
    assert m["paper"]["once_argv"] == ["-m", "cherrypick.automations.paper_loop", "--once"]
    assert m["paper"]["log"] == "automations_paper.log"
    # Stored names stay: the ledger's folder resolves under either name, the schema is stored data.
    assert m["paper"]["paper_db"] == OLD_BLOCK["paper"]["paper_db"]
    assert m["paper"]["trade_schema"] == "ensemble_trade"
    assert cfg["modules"]["ensemble"] is OLD_BLOCK  # the caller's dict is left alone


def test_a_config_naming_both_keeps_the_new_block_once():
    # The new block first, the old after it: the old must not replace it on the way through.
    cfg = {"modules": {"automations": {"enabled": False, "path": "../automations"}, "ensemble": OLD_BLOCK}}
    out = cfgmod.resolve_renamed(cfg)
    assert list(out["modules"]) == ["automations"] and out["modules"]["automations"]["enabled"] is False
    assert cfgmod.resolve_renamed({"modules": {"meic": {}}}) == {"modules": {"meic": {}}}


def test_load_config_resolves_the_old_key(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"modules": {"ensemble": OLD_BLOCK}}), encoding="utf-8")
    assert list(cfgmod.load_config(p)["modules"]) == ["automations"]


def test_a_stamp_from_before_the_move_reads_the_file_where_it_lives_now(tmp_path):
    new = tmp_path / "packages/automations/src/cherrypick/automations/engine.py"
    new.parent.mkdir(parents=True)
    new.write_bytes(b"print('engine')\n")
    old_rel = "packages/ensemble/src/cherrypick/ensemble/engine.py"
    digest = hashlib.sha256(new.read_bytes()).hexdigest()
    assert sv.file_hashes([old_rel], tmp_path) == {old_rel: digest}
    stamped = {"version": 0, "depends": {old_rel: digest}}
    assert sv.state(stamped, [], tmp_path)["stale"] is False
    new.write_bytes(b"print('changed')\n")
    assert sv.state(stamped, [], tmp_path)["changed_files"] == [old_rel]
    assert sv.file_hashes(["packages/gone.py"], tmp_path) == {"packages/gone.py": None}


def test_a_script_importing_the_old_name_rests_on_the_new_file(tmp_path):
    new = tmp_path / "packages/automations/src/cherrypick/automations/engine.py"
    new.parent.mkdir(parents=True)
    new.write_text("X = 1\n", encoding="utf-8")
    script = tmp_path / "packages/backtest/scripts/study_x.py"
    script.parent.mkdir(parents=True)
    script.write_text("from cherrypick.ensemble import engine\n", encoding="utf-8")
    deps = sv.dependencies(script, tmp_path)
    assert "packages/automations/src/cherrypick/automations/engine.py" in deps
