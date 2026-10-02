"""A fresh install has no earnings config of its own; the module must read the shipped example
rather than open a path that does not exist."""

from __future__ import annotations

from cherrypick.earnings import paths


def test_a_fresh_install_reads_the_shipped_config_example(tmp_path, monkeypatch):
    monkeypatch.setattr(paths._home, "config_path", lambda name=None: tmp_path / "absent.json")
    monkeypatch.setattr(paths, "_PKG_ROOT", tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.example.json").write_text("{}", encoding="utf-8")
    assert paths.config_path() == tmp_path / "config" / "config.example.json"
    (tmp_path / "config" / "config.json").write_text("{}", encoding="utf-8")
    assert paths.config_path() == tmp_path / "config" / "config.json"
    home = tmp_path / "home.json"
    home.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(paths._home, "config_path", lambda name=None: home)
    assert paths.config_path() == home
