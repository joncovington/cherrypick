"""Session-wide test setup: the core submodule on sys.path, and a managed home that is never real.

Autouse `CHERRYPICK_HOME` → a throwaway dir for every test, so nothing a test triggers can write the
real `~/.cherrypick` (the failure the flies suite learned the hard way — a test writing the managed home
mid-session). gex reads/writes there too now (its stream-request registration), so it needs the same
guard.
"""

import sys
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parent.parent / "src" / "_core"
if _CORE.is_dir() and str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture(autouse=True)
def managed_home(tmp_path, monkeypatch):
    home = tmp_path / "cherrypick-home"
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    return home
