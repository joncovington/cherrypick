"""Every test runs against a throwaway cherrypick home.

Autouse, deliberately: a test that forgets to redirect the home would read — and in the write tests,
*write into* — the developer's real `~/.cherrypick`. `CHERRYPICK_HOME` is the suite's master
override, so setting it relocates config, state and every package's data in one move, which is
exactly the isolation these tests need (the write-confinement test snapshots the whole tree).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    home = tmp_path / "cherrypick-home"
    home.mkdir()
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    # Narrow per-scope overrides would defeat the master override; make sure none leaked in from
    # the developer's shell.
    for leaked in (
        "ADVISOR_DATA_DIR",
        "ADVISOR_LOGS_DIR",
        "MEIC_DATA_DIR",
        "EARNINGS_DATA_DIR",
        "FLIES_DATA_DIR",
        "REVIEW_DATA_DIR",
    ):
        monkeypatch.delenv(leaked, raising=False)
    return home
