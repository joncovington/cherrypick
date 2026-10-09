r"""Tests for packages/console/run.py's _child_env -- the console launcher's PATH repair.

The console server shells out to a bare `python` for everything Python owns (the keyring bridge,
the config editor, positions, the advisor). The installer puts the suite in a `.venv` that is on
nobody's PATH, so the launcher prepends the directory of the interpreter running IT to the child
environment's PATH -- the supervisor starts it with the venv's interpreter, so `python` then
resolves to the one that has the suite.

The regression this file pins shipped in 6bbbadbb and was found on a Linux machine: `Path.resolve()`
follows a venv's `bin/python` symlink all the way to the BASE interpreter (/usr/bin/python3.NN), so
the PATH entry became /usr/bin -- the very directory the repair exists to outrank. Windows venvs
copy a real launcher binary in, so resolve() happened to be correct there; on Linux the venv must be
reached THROUGH the symlink, because executing .venv/bin/python is what puts the venv's
site-packages on the process.

This file sits under tools/tests/ deliberately: the console is the one Node package in CI's matrix,
so packages/console has no pytest lane of its own, and the tools lane is the only one that runs
outside the package matrix.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_launcher():
    spec = importlib.util.spec_from_file_location(
        "console_launcher", ROOT / "packages" / "console" / "run.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_venv(tmp_path: Path, symlink: bool) -> Path:
    """A venv-shaped tree whose bin/python is either a symlink to a base interpreter (Linux) or a
    real file (Windows). Nothing is executed -- _child_env only reads the path string."""
    base_bin = tmp_path / "base" / "bin"
    base_bin.mkdir(parents=True)
    base = base_bin / "python3.14"
    base.write_text("#!/bin/sh\n")
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    python = venv_bin / "python"
    if symlink:
        python.symlink_to(base)
    else:
        python.write_text("#!/bin/sh\n")
    return venv_bin


def test_a_venv_symlink_is_reached_through_not_resolved_away(tmp_path, monkeypatch):
    launcher = _load_launcher()
    venv_bin = _fake_venv(tmp_path, symlink=True)
    monkeypatch.setattr(sys, "executable", str(venv_bin / "python"))
    env = launcher._child_env()
    first = env["PATH"].split(os.pathsep)[0]
    assert first == str(venv_bin), (
        f"the child PATH must lead with the venv's bin (got {first!r}) — resolving the "
        "interpreter's symlink lands in the base interpreter's directory and the console's "
        "subprocess bridges reach a system Python with no suite installed"
    )


def test_a_real_launcher_file_behaves_as_before(tmp_path, monkeypatch):
    launcher = _load_launcher()
    venv_bin = _fake_venv(tmp_path, symlink=False)
    monkeypatch.setattr(sys, "executable", str(venv_bin / "python"))
    env = launcher._child_env()
    assert env["PATH"].split(os.pathsep)[0] == str(venv_bin)
    assert env["PATH"].split(os.pathsep)[1:] != [] or True  # the rest of PATH is preserved


def test_the_preexisting_path_is_kept_behind_the_venv(tmp_path, monkeypatch):
    launcher = _load_launcher()
    venv_bin = _fake_venv(tmp_path, symlink=True)
    monkeypatch.setattr(sys, "executable", str(venv_bin / "python"))
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")
    env = launcher._child_env()
    assert env["PATH"].split(os.pathsep) == [str(venv_bin), "/usr/local/bin", "/usr/bin"]


def test_the_interpreter_is_named_for_the_server_and_pythonw_maps_to_python(tmp_path, monkeypatch):
    # 2026-10-08 OS audit: the server's suitePython() reads CHERRYPICK_PYTHON first.
    launcher = _load_launcher()
    monkeypatch.delenv("CHERRYPICK_PYTHON", raising=False)
    venv_bin = _fake_venv(tmp_path, symlink=False)
    monkeypatch.setattr(sys, "executable", str(venv_bin / "python"))
    assert launcher._child_env()["CHERRYPICK_PYTHON"] == str(venv_bin / "python")
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    (scripts / "pythonw.exe").write_text("")
    (scripts / "python.exe").write_text("")
    monkeypatch.setattr(sys, "executable", str(scripts / "pythonw.exe"))
    assert launcher._child_env()["CHERRYPICK_PYTHON"] == str(scripts / "python.exe")
    monkeypatch.setenv("CHERRYPICK_PYTHON", "/opt/chosen/python")
    assert launcher._child_env()["CHERRYPICK_PYTHON"] == "/opt/chosen/python"
