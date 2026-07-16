"""Module-location resolution: explicit `path` override vs. the managed ~/.cherrypick/modules home."""

from __future__ import annotations

import pytest

from cherrypick.orchestrator import config as c

MEIC_REPO = "https://github.com/joncovington/cherrypick-meic.git"


def test_module_dirname_from_repo_strips_git():
    assert c.module_dirname({"repo": MEIC_REPO}) == "cherrypick-meic"
    assert c.module_dirname({"repo": "https://github.com/x/cherrypick-meic"}) == "cherrypick-meic"
    assert c.module_dirname({"repo": "git@github.com:x/cherrypick-earnings.git"}) == "cherrypick-earnings"


def test_module_dirname_falls_back_to_name_without_repo():
    assert c.module_dirname({}, "meic") == "meic"


def test_module_dirname_requires_repo_or_name():
    with pytest.raises(ValueError):
        c.module_dirname({})


def test_module_root_explicit_relative_is_anchored_at_root():
    assert c.module_root({"path": "../cherrypick-meic"}) == (c.ROOT / "../cherrypick-meic").resolve()


def test_module_root_absolute_path_wins(tmp_path):
    assert c.module_root({"path": str(tmp_path)}) == tmp_path.resolve()


def test_module_root_path_overrides_repo(tmp_path):
    # An explicit path is the dev checkout and must win over repo (which would resolve to modules home).
    assert c.module_root({"repo": MEIC_REPO, "path": str(tmp_path)}, "meic") == tmp_path.resolve()


def test_module_root_defaults_to_modules_home_by_repo(monkeypatch, tmp_path):
    monkeypatch.setattr(c, "MODULES_HOME", tmp_path)
    assert c.module_root({"repo": MEIC_REPO}, "meic") == (tmp_path / "cherrypick-meic").resolve()


def test_module_root_defaults_to_modules_home_by_name(monkeypatch, tmp_path):
    monkeypatch.setattr(c, "MODULES_HOME", tmp_path)
    assert c.module_root({}, "earnings") == (tmp_path / "earnings").resolve()


def test_source_root_honors_cherrypick_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    assert c._source_root() == tmp_path


def test_source_root_uses_repo_root_in_source_checkout(monkeypatch):
    # No env override, running from the source tree -> the repo root (has run.py + pyproject.toml).
    # ROOT is the *source anchor* for relative module paths; runtime files live under the per-user home.
    monkeypatch.delenv("CHERRYPICK_HOME", raising=False)
    root = c._source_root()
    assert (root / "run.py").exists() or (root / "pyproject.toml").exists()


def test_eod_digest_settings_default_on():
    # A config with no eod_digest section still schedules the digest (on by default) at defaults.
    s = c.eod_digest_settings({"modules": {}})
    assert s == {"enabled": True, "task_name": "cherrypick-eod-digest", "at": "16:15"}


def test_eod_digest_settings_opt_out_and_overrides():
    assert c.eod_digest_settings({"eod_digest": {"enabled": False}})["enabled"] is False
    s = c.eod_digest_settings({"eod_digest": {"task_name": "my-eod", "at": "17:00"}})
    assert s["enabled"] is True and s["task_name"] == "my-eod" and s["at"] == "17:00"
