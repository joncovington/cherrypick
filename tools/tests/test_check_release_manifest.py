"""Tests for tools/check_release_manifest.py -- the release workflow's refusal of a tag with no manifest
entry. What must not happen: a release published that `run.py upgrade` cannot describe."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_release_manifest as crm  # noqa: E402

GOOD = {
    "upgrading": ["Read this."],
    "manual_steps": [],
    "config_changes": [{"file": "meic.json", "key": "stop.mode", "note": "renamed"}],
    "measurement_boundaries": [],
    "migrations": ["v0.13.0-01-thing"],
}


def manifest(**entry) -> dict:
    return {"schema": 1, "releases": {"v0.13.0": {**GOOD, **entry}}}


def test_a_complete_entry_passes():
    assert crm.problems(manifest(), "v0.13.0") == []


def test_a_tag_with_no_entry_is_refused():
    out = crm.problems(manifest(), "v0.14.0")
    assert out == ["no entry for v0.14.0 in release/manifest.json"]


def test_an_entry_without_upgrading_notes_is_refused():
    assert any("`upgrading` is empty" in p for p in crm.problems(manifest(upgrading=[]), "v0.13.0"))


def test_a_missing_or_mistyped_field_is_refused():
    entry = dict(GOOD)
    del entry["manual_steps"]
    bad = {"releases": {"v0.13.0": entry}}
    assert any("manual_steps" in p for p in crm.problems(bad, "v0.13.0"))
    assert any("config_changes" in p for p in crm.problems(manifest(config_changes=["meic.json"]), "v0.13.0"))
    assert any("migrations" in p for p in crm.problems(manifest(migrations=["rename-things"]), "v0.13.0"))
    assert any(
        "repeats" in p for p in crm.problems(manifest(migrations=["v0.13.0-01-a", "v0.13.0-01-a"]), "v0.13.0")
    )


def test_a_malformed_manifest_or_tag_is_refused():
    assert crm.problems([], "v0.13.0")
    assert crm.problems({"releases": []}, "v0.13.0")
    assert crm.problems(manifest(), "0.13.0")
    assert crm.problems(manifest(), "v0.13.0-rc1")


def test_a_beta_is_a_tag_with_its_own_entry():
    beta = {"schema": 1, "releases": {"v0.13.0-beta": dict(GOOD, migrations=[])}}
    assert crm.problems(beta, "v0.13.0-beta") == []
    assert crm.problems(beta, "v0.13.0-beta.2") == ["no entry for v0.13.0-beta.2 in release/manifest.json"]


def test_the_real_manifest_has_the_first_tooled_release():
    real = json.loads(crm.MANIFEST.read_text(encoding="utf-8"))
    assert crm.problems(real, "v0.12.0") == []
    assert real["releases"]["v0.12.0"]["first_with_upgrade_tooling"] is True


def test_the_command_exits_one_with_a_line_per_problem(tmp_path, capsys):
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest()), encoding="utf-8")
    assert crm.main(["v0.13.0", "--manifest", str(path)]) == 0
    assert crm.main(["v0.14.0", "--manifest", str(path)]) == 1
    assert "no entry for v0.14.0" in capsys.readouterr().err
    assert crm.main(["v0.13.0", "--manifest", str(tmp_path / "missing.json")]) == 1
