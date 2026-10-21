"""The manage snapshot's version against the installed code's, pinned.

A merge that raises SNAPSHOT_VERSION after the day's last manage run leaves the stored snapshot older
than the code until the next run, and the views needing the newer fields read empty meanwhile
(2026-10-20: v11 under v14). The watchdog says so; it never takes the snapshot itself.
"""

import json

import pytest

from cherrypick.orchestrator import managesnap
from cherrypick.orchestrator import watchdog as wd

pytestmark = pytest.mark.unit

ON = {"manage": {"enabled": True}}


def _store(monkeypatch, tmp_path, versions):
    monkeypatch.setenv("MANAGE_DATA_DIR", str(tmp_path))
    for session, version in versions.items():
        doc = {"snapshot_version": version, "session": session, "positions": {"x": "y" * 2000}}
        (tmp_path / f"snapshot-{session}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_an_older_snapshot_than_the_code_warns_naming_both_versions(monkeypatch, tmp_path):
    old = managesnap.SNAPSHOT_VERSION - 3
    _store(monkeypatch, tmp_path, {"2026-10-19": managesnap.SNAPSHOT_VERSION, "2026-10-20": old})
    (f,) = wd._check_manage_snapshot(ON)
    assert f.key == "manage.snapshot_version" and f.status == wd.WARN
    assert f"2026-10-20 snapshot is v{old}" in f.message
    assert f"code takes v{managesnap.SNAPSHOT_VERSION}" in f.message


def test_a_current_snapshot_is_ok(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path, {"2026-10-20": managesnap.SNAPSHOT_VERSION})
    (f,) = wd._check_manage_snapshot(ON)
    assert f.status == wd.OK


def test_the_version_is_read_from_the_whole_file_when_the_head_lacks_it(monkeypatch, tmp_path):
    monkeypatch.setenv("MANAGE_DATA_DIR", str(tmp_path))
    doc = {"positions": {"x": "y" * 2000}, "snapshot_version": 1}
    (tmp_path / "snapshot-2026-10-20.json").write_text(json.dumps(doc), encoding="utf-8")
    (f,) = wd._check_manage_snapshot(ON)
    assert f.status == wd.WARN and "is v1 " in f.message


def test_nothing_is_said_while_manage_is_off_or_has_no_snapshot(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path, {"2026-10-20": 1})
    assert wd._check_manage_snapshot({"manage": {"enabled": False}}) == []
    monkeypatch.setenv("MANAGE_DATA_DIR", str(tmp_path / "empty"))
    assert wd._check_manage_snapshot(ON) == []
