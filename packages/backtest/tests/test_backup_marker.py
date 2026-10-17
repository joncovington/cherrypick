"""The orchestrator's backup skips licensed vendor data by this package's marker file. The two
packages never import each other, so the name is spelt in both; this holds them equal."""

from __future__ import annotations

import re
from pathlib import Path

from cherrypick.backtest import paths

BACKUP = (
    Path(__file__).resolve().parents[2] / "orchestrator" / "src" / "cherrypick" / "orchestrator" / "backup.py"
)


def test_the_backup_skips_the_same_marker_this_package_writes():
    text = BACKUP.read_text(encoding="utf-8")
    found = re.search(r'^LICENSED_MARKER = "([^"]+)"', text, re.M)
    assert found, "orchestrator/backup.py no longer declares LICENSED_MARKER"
    assert found.group(1) == paths.LICENSED_MARKER
