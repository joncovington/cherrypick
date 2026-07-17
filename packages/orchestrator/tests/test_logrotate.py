"""Tests for end-of-month log/report rotation (orchestrator.logrotate).

Files-only maintenance lane: build a temp logs tree (suite + module subdirs) spanning two months plus
rotated logs and an active .log, then assert that only complete prior months are zipped into
logs/archive/<YYYY-MM>/<scope>.zip, originals are removed after the zip verifies, the current month and
the active .log are left alone, and a re-run is idempotent.
"""

import os
import zipfile
from datetime import datetime

import pytest

from cherrypick.orchestrator import logrotate

pytestmark = pytest.mark.unit


def _set_mtime(p, dt):
    ts = dt.timestamp()
    os.utime(p, (ts, ts))


def _tree(root):
    """A logs tree with June (finished) and July (current) reports across suite + two module dirs, a
    rotated log backup (mtime pinned to July so it groups with the current month), and an active .log."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "eod-digest-2026-06-10.md").write_text("june digest", encoding="utf-8")
    (root / "eod-digest-2026-07-03.md").write_text("july digest", encoding="utf-8")
    for mod in ("meic", "earnings"):
        d = root / mod
        d.mkdir()
        (d / "paper-eod-2026-06-11.md").write_text("june", encoding="utf-8")
        (d / "eod-analysis-2026-06-11.md").write_text("june analysis", encoding="utf-8")
        (d / "paper-eod-2026-07-02.md").write_text("july", encoding="utf-8")
        (d / "paper_loop.log").write_text("active", encoding="utf-8")  # must be left alone
        rotated = d / "paper_loop.log.1"
        rotated.write_text("rotated", encoding="utf-8")
        _set_mtime(rotated, datetime(2026, 7, 5))  # current month -> kept this run


def test_archives_only_finished_months(tmp_path):
    root = tmp_path / "logs"
    _tree(root)
    now = datetime(2026, 7, 15, 3, 30)

    res = logrotate.run(logs_root=root, now=now)
    assert res["ok"]

    # June files are zipped per scope; the archive tree exists.
    for scope, name in (("suite", "eod-digest-2026-06-10.md"),
                        ("meic", "paper-eod-2026-06-11.md"),
                        ("earnings", "eod-analysis-2026-06-11.md")):
        z = root / "archive" / "2026-06" / f"{scope}.zip"
        assert z.exists(), f"missing {z}"
        with zipfile.ZipFile(z) as zf:
            assert name in zf.namelist()

    # June originals are gone; July (current month) + the active .log stay.
    assert not (root / "eod-digest-2026-06-10.md").exists()
    assert not (root / "meic" / "paper-eod-2026-06-11.md").exists()
    assert (root / "eod-digest-2026-07-03.md").exists()
    assert (root / "meic" / "paper-eod-2026-07-02.md").exists()
    assert (root / "meic" / "paper_loop.log").exists()
    # The rotated backup's mtime is "now" (July), so it is not archived yet.
    assert (root / "meic" / "paper_loop.log.1").exists()

    # No July zip was created (nothing finished for July).
    assert not (root / "archive" / "2026-07").exists()


def test_rotated_log_backup_archived_by_mtime(tmp_path):
    root = tmp_path / "logs"
    _tree(root)
    # Age the rotated backup into June so it groups with the finished month.
    _set_mtime(root / "meic" / "paper_loop.log.1", datetime(2026, 6, 20))

    logrotate.run(logs_root=root, now=datetime(2026, 7, 15))
    z = root / "archive" / "2026-06" / "meic.zip"
    with zipfile.ZipFile(z) as zf:
        assert "paper_loop.log.1" in zf.namelist()
    assert not (root / "meic" / "paper_loop.log.1").exists()


def test_dry_run_writes_nothing(tmp_path):
    root = tmp_path / "logs"
    _tree(root)
    res = logrotate.run(logs_root=root, now=datetime(2026, 7, 15), dry_run=True)
    assert res["dry_run"] and res["files"] > 0
    assert not (root / "archive").exists()
    assert (root / "eod-digest-2026-06-10.md").exists()  # nothing deleted


def test_month_filter_and_idempotent_rerun(tmp_path):
    root = tmp_path / "logs"
    _tree(root)
    # Add a May file so we can prove --month scopes to one month.
    (root / "eod-digest-2026-05-09.md").write_text("may", encoding="utf-8")

    res = logrotate.run(logs_root=root, now=datetime(2026, 7, 15), month="2026-06")
    assert res["files"] > 0
    assert (root / "eod-digest-2026-05-09.md").exists()  # May untouched by the filter
    assert not (root / "eod-digest-2026-06-10.md").exists()  # June archived

    # Re-running is a no-op that must not raise or lose the archive.
    res2 = logrotate.run(logs_root=root, now=datetime(2026, 7, 15), month="2026-06")
    assert res2["ok"] and res2["files"] == 0
    with zipfile.ZipFile(root / "archive" / "2026-06" / "suite.zip") as zf:
        assert "eod-digest-2026-06-10.md" in zf.namelist()
