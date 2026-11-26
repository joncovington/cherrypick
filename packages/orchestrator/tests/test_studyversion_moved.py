"""A study stamped before a package folder moved is not stale for the move alone (`studyversion.MOVED`):
the stamp names the old path, and the file is read where it lives now."""

from __future__ import annotations

import hashlib

from cherrypick.orchestrator import studyversion as sv


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
