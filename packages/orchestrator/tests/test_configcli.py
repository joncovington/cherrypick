"""configcli: the stdin/stdout front-end the console reaches config through.

The claims that matter to a non-Python caller: a refusal is data (`ok: false` + a `code`) on exit 0,
so "the config said no" is never confused with "the bridge is broken"; a multi-edit save is one
atomic write that keeps the file's documentation intact; and the guarded live pointers are refused
here exactly as they are over HTTP. Driven through a real subprocess, because the contract IS the
process boundary — an in-process call would not prove the exit statuses or the stdout framing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SRC = Path(__file__).resolve().parent.parent / "src"

FLIES_LIKE = """{
  "_comment": "documentation that a json round trip would erase",
  "live": {
    "enabled": false,
    "gate0_confirmed": ""
  },
  "symbols": [
    "XSP"
  ],
  "defaults": {
    "wing_width": 1,
    "max_positions": 4
  },
  "arms": {
    "gex": {
      "enabled": true
    },
    "control": {
      "enabled": false
    }
  }
}
"""

ORCHESTRATOR_LIKE = {
    "timezone": "America/New_York",
    "modules": {"flies": {"enabled": True, "path": "../flies"}},
}


@pytest.fixture
def home(tmp_path):
    """A sandbox cherrypick home with an orchestrator config and a flies config."""
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "flies.json").write_text(FLIES_LIKE, encoding="utf-8")
    (tmp_path / "config.json").write_text(json.dumps(ORCHESTRATOR_LIKE, indent=2), encoding="utf-8")
    return tmp_path


def call(home: Path, request: dict) -> tuple[int, dict, str]:
    """Run the bridge as the console runs it. Returns (status, parsed stdout, stderr)."""
    env = {**os.environ, "CHERRYPICK_HOME": str(home), "PYTHONPATH": str(_SRC)}
    out = subprocess.run(
        [sys.executable, "-m", "cherrypick.orchestrator.configcli"],
        input=json.dumps(request),
        capture_output=True,
        encoding="utf-8",
        env=env,
    )
    try:
        parsed = json.loads(out.stdout) if out.stdout.strip() else {}
    except json.JSONDecodeError:
        parsed = {}
    return out.returncode, parsed, out.stderr


# --- read ----------------------------------------------------------------------------------------


def test_load_returns_doc_guarded_pointers_and_mtime(home):
    status, body, _ = call(home, {"op": "load", "target": "flies"})
    assert status == 0 and body["ok"] is True
    assert body["doc"]["arms"]["gex"]["enabled"] is True
    assert isinstance(body["mtime"], int)
    guarded = {g["pointer"] for g in body["guarded"]}
    assert "/live/enabled" in guarded and "/live/gate0_confirmed" in guarded


def test_targets_lists_the_editable_configs(home):
    status, body, _ = call(home, {"op": "targets"})
    assert status == 0 and body["ok"] is True
    ids = {t["id"] for t in body["targets"]}
    assert {"orchestrator", "flies", "meic"} <= ids


# --- save ----------------------------------------------------------------------------------------


def test_multi_edit_save_is_one_write_that_keeps_the_documentation(home):
    path = home / "config" / "flies.json"
    mtime = path.stat().st_mtime_ns
    status, body, _ = call(
        home,
        {
            "op": "save",
            "target": "flies",
            "expected_mtime": mtime,
            "edits": [
                {"pointer": "/arms/control/enabled", "value": True},
                {"pointer": "/defaults/max_positions", "value": 6},
            ],
        },
    )
    assert status == 0 and body["ok"] is True

    text = path.read_text(encoding="utf-8")
    doc = json.loads(text)
    assert doc["arms"]["control"]["enabled"] is True
    assert doc["defaults"]["max_positions"] == 6
    # The comment and the untouched keys survive byte-for-byte — the reason this goes through
    # configedit rather than a json round trip.
    assert '"_comment": "documentation that a json round trip would erase"' in text
    assert text.count("\n") == FLIES_LIKE.count("\n")

    backups = list((home / "state" / "config-backups").glob("flies.*.json"))
    assert len(backups) == 1, "a section save is one backup, not one per field"
    assert backups[0].read_text(encoding="utf-8") == FLIES_LIKE


def test_guarded_pointer_is_refused_as_data_not_a_crash(home):
    before = (home / "config" / "flies.json").read_text(encoding="utf-8")
    status, body, _ = call(
        home,
        {"op": "save", "target": "flies", "edits": [{"pointer": "/live/enabled", "value": True}]},
    )
    assert status == 0, "a refusal is data on exit 0, not a broken bridge"
    assert body["ok"] is False and body["code"] == "guarded"
    assert "/live/enabled" in body["error"]
    assert (home / "config" / "flies.json").read_text(encoding="utf-8") == before


def test_stale_mtime_is_a_conflict(home):
    status, body, _ = call(
        home,
        {
            "op": "save",
            "target": "flies",
            "expected_mtime": 1,
            "edits": [{"pointer": "/defaults/wing_width", "value": 2}],
        },
    )
    assert status == 0 and body["ok"] is False and body["code"] == "conflict"


def test_a_bad_pointer_leaves_the_file_alone(home):
    before = (home / "config" / "flies.json").read_text(encoding="utf-8")
    status, body, _ = call(
        home,
        {"op": "save", "target": "flies", "edits": [{"pointer": "/defaults/nope", "value": 1}]},
    )
    assert status == 0 and body["ok"] is False and body["code"] == "not_found"
    assert (home / "config" / "flies.json").read_text(encoding="utf-8") == before


def test_one_bad_edit_rejects_the_whole_batch(home):
    """A part-applied section save would be worse than a refused one."""
    before = (home / "config" / "flies.json").read_text(encoding="utf-8")
    status, body, _ = call(
        home,
        {
            "op": "save",
            "target": "flies",
            "edits": [
                {"pointer": "/defaults/wing_width", "value": 2},
                {"pointer": "/live/gate0_confirmed", "value": "nope"},
            ],
        },
    )
    assert status == 0 and body["ok"] is False and body["code"] == "guarded"
    assert (home / "config" / "flies.json").read_text(encoding="utf-8") == before


# --- halt ----------------------------------------------------------------------------------------


def test_halt_set_clear_and_status_are_idempotent(home):
    flag = home / "state" / "halt-live.flag"

    _, body, _ = call(home, {"op": "halt_status"})
    assert body["present"] is False

    for _ in range(2):
        _, body, _ = call(home, {"op": "set_halt", "present": True})
        assert body["ok"] is True and body["present"] is True and flag.exists()

    _, body, _ = call(home, {"op": "halt_status"})
    assert body["present"] is True

    for _ in range(2):
        _, body, _ = call(home, {"op": "set_halt", "present": False})
        assert body["ok"] is True and body["present"] is False and not flag.exists()


# --- framing -------------------------------------------------------------------------------------


def test_unknown_op_is_a_refusal_not_a_failure(home):
    status, body, _ = call(home, {"op": "nope"})
    assert status == 0 and body["ok"] is False and body["code"] == "bad_request"


def test_malformed_request_fails_loudly(home):
    env = {**os.environ, "CHERRYPICK_HOME": str(home), "PYTHONPATH": str(_SRC)}
    out = subprocess.run(
        [sys.executable, "-m", "cherrypick.orchestrator.configcli"],
        input="not json",
        capture_output=True,
        encoding="utf-8",
        env=env,
    )
    assert out.returncode != 0 and "not valid JSON" in out.stderr
