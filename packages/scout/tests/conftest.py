"""Session-wide test setup: a managed home that is never real.

Autouse `CHERRYPICK_HOME` -> a throwaway dir for every test, so nothing a test triggers can write the
real `~/.cherrypick` (the failure the flies suite learned the hard way).
"""

import pytest


@pytest.fixture(autouse=True)
def managed_home(tmp_path, monkeypatch):
    home = tmp_path / "cherrypick-home"
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    return home
