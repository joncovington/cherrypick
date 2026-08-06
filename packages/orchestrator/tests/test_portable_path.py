"""`config.portable_path` must never emit an absolute path, on any platform.

The regression this locks in: the fallback used `os.path.relpath`, which walks up out of ROOT and
back down (`../../../tmp/pytest-of-runner/<test-name>/nope`) rather than refusing. That keeps every
original segment, so the "no absolute path on any surface" guardrail silently held only on Windows,
where a different drive raises ValueError. It surfaced the first time orchestrator CI ran on Linux.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cherrypick.orchestrator import config as cfgmod

pytestmark = pytest.mark.unit


@pytest.fixture
def anchored(tmp_path, monkeypatch):
    """Pin home and ROOT to controlled dirs so the three branches are testable on any platform."""
    home = tmp_path / "home"
    root = tmp_path / "repo"
    home.mkdir()
    root.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(cfgmod, "ROOT", root)
    return home, root


def test_home_collapses_to_tilde(anchored):
    home, _ = anchored
    assert cfgmod.portable_path(home / ".cherrypick" / "config.json") == "~/.cherrypick/config.json"


def test_under_root_is_relative(anchored):
    _, root = anchored
    assert cfgmod.portable_path(root / "packages" / "meic") == "packages/meic"


def test_outside_both_falls_back_to_basename_never_a_walk_up(tmp_path, anchored):
    """The actual regression: a path under neither anchor must not be rendered by walking up."""
    outside = tmp_path / "elsewhere" / "test_service_missing_checkout_0" / "nope"
    out = cfgmod.portable_path(outside)
    assert out == "nope"
    assert ".." not in out
    assert "test_service_missing_checkout_0" not in out
    assert str(tmp_path) not in out
