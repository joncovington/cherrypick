"""The EOD card must find reports the monthly archiver has rotated away.

The bug this pins: `_eod_view` decided a report existed by stat-ing the live path only, while
`logrotate.run` deletes those originals once a month closes and keeps them in
`logs/archive/<YYYY-MM>/<scope>.zip`. On the first of every month the last trading session is still
inside the month just archived, so every module rendered "no files yet" — while `/eod-report`, which
had already learned to read the archive, would have served the very same files without complaint.
Half the fix had landed; this is the other half, plus the shared lookup both sides now use.
"""

from __future__ import annotations

import zipfile

import pytest

from cherrypick.orchestrator import logrotate

pytestmark = pytest.mark.unit

REPORT = "paper-eod-2026-07-31.md"
SESSION = "2026-07-31"
BODY = "# Flies paper — 2026-07-31\n\nnet -$2,278.40\n"


@pytest.fixture
def logs_root(tmp_path):
    """A logs home whose July reports have been archived and removed, as the archiver leaves it."""
    archive = tmp_path / "archive" / "2026-07"
    archive.mkdir(parents=True)
    with zipfile.ZipFile(archive / "flies.zip", "w") as zf:
        zf.writestr(REPORT, BODY)  # bare filename arcname, matching _archive_into
    (tmp_path / "flies").mkdir()  # module log dir exists, but the report is gone
    return tmp_path


def test_archived_report_is_found_after_the_original_is_deleted(logs_root):
    assert logrotate.archived_report_exists(logs_root, "flies", REPORT, SESSION)
    assert logrotate.archived_report_text(logs_root, "flies", REPORT, SESSION) == BODY


def test_a_report_that_never_existed_is_still_absent(logs_root):
    assert not logrotate.archived_report_exists(logs_root, "flies", "paper-eod-1999-01-01.md", "1999-01-01")
    assert logrotate.archived_report_text(logs_root, "flies", "paper-eod-2026-07-30.md", "2026-07-30") is None


def test_the_session_selects_the_month_archive(logs_root):
    """The lookup is derived from the session's month, so a report is only sought where the archiver
    would have put it — an August session must not find a July archive's contents."""
    assert logrotate.archive_zip_for(logs_root, "flies", SESSION).name == "flies.zip"
    assert logrotate.archive_zip_for(logs_root, "flies", SESSION).parent.name == "2026-07"
    assert not logrotate.archived_report_exists(logs_root, "flies", REPORT, "2026-08-01")


def test_scopes_do_not_leak_into_one_another(logs_root):
    """`scope` is the module name (or "suite"); meic must not resolve out of flies' archive."""
    assert not logrotate.archived_report_exists(logs_root, "meic", REPORT, SESSION)


def test_a_corrupt_archive_reads_as_unavailable_not_an_exception(logs_root):
    """Best-effort by design: a damaged zip must degrade to "no report", never take down the render
    that asked for it (this runs on every dashboard rebuild)."""
    (logs_root / "archive" / "2026-07" / "meic.zip").write_bytes(b"not a zip at all")
    assert logrotate.archived_report_exists(logs_root, "meic", REPORT, SESSION) is False
    assert logrotate.archived_report_text(logs_root, "meic", REPORT, SESSION) is None
