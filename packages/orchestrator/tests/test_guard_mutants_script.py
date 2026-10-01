"""The morning guard check (`scripts/guard_mutants.py`) -- itself a guard, so it must be able to fail.

These drive its verdict logic with a stubbed pytest runner, so they cost nothing and run in CI; the
real mutants run in the scheduled job. Lives here because the orchestrator schedules it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _load(name: str):
    """Registered in sys.modules before executing: its dataclasses resolve their module there, and
    the runner's own `import guard_mutants_plugin` then gets this same object."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


plugin = _load("guard_mutants_plugin")
runner = _load("guard_mutants")


@pytest.mark.parametrize("mutant", plugin.MUTANTS, ids=lambda m: m.id)
def test_every_mutant_target_still_exists(mutant):
    """A rename must fail loudly here, not leave a mutant patching nothing every morning."""
    owner, name = plugin.resolve(mutant)
    assert hasattr(owner, name)


@pytest.mark.parametrize("mutant", plugin.MUTANTS, ids=lambda m: m.id)
def test_every_mutant_names_a_test_that_exists(mutant):
    root = Path(__file__).resolve().parents[2] / mutant.package
    for node in mutant.tests:
        path, _, func = node.partition("::")
        assert (root / path).exists(), node
        assert f"def {func}(" in (root / path).read_text(encoding="utf-8"), node


def _stub(monkeypatch, *, clean=(0, ""), mutated=(1, "E       AssertionError: assert 1 == 0")):
    def fake(package, tests, mutant_id=None):
        return mutated if mutant_id else clean

    monkeypatch.setattr(runner, "run_pytest", fake)


def test_a_mutant_killed_by_an_assertion_is_ok(monkeypatch):
    _stub(monkeypatch)
    report = runner.check(plugin.MUTANTS[:1])
    assert report["ok"] and report["mutants"][0]["verdict"] == "killed"


def test_a_surviving_mutant_fails_the_check(monkeypatch):
    _stub(monkeypatch, mutated=(0, "1 passed"))
    report = runner.check(plugin.MUTANTS[:1])
    assert not report["ok"] and report["mutants"][0]["verdict"] == "survived"


def test_a_crash_is_not_a_kill(monkeypatch):
    _stub(monkeypatch, mutated=(1, "E   TypeError: unsupported operand"))
    report = runner.check(plugin.MUTANTS[:1])
    assert not report["ok"] and report["mutants"][0]["verdict"] == "error"


def test_a_failing_clean_run_fails_the_check_without_trying_mutants(monkeypatch):
    calls = []

    def fake(package, tests, mutant_id=None):
        calls.append(mutant_id)
        return (1, "E   assert False") if mutant_id is None else (1, "E   AssertionError")

    monkeypatch.setattr(runner, "run_pytest", fake)
    report = runner.check(plugin.MUTANTS[:1])
    assert not report["ok"] and calls == [None]


def _quiet_main(monkeypatch, tmp_path, *, fingerprint, last):
    """Drive main() with the checkout, the last record and the state file stubbed; return whether
    the mutants actually ran."""
    ran = []
    monkeypatch.setattr(runner, "checkout_fingerprint", lambda: fingerprint)
    monkeypatch.setattr(runner, "_last_report", lambda: last)
    monkeypatch.setattr(runner, "_state_path", lambda: tmp_path / "guard-mutants.last.json")
    monkeypatch.setattr(
        runner, "check", lambda ms: ran.append(1) or {"ok": True, "baseline": {}, "mutants": []}
    )
    assert runner.main(["--if-changed"]) == 0
    return bool(ran)


def test_if_changed_skips_a_checkout_that_already_passed(monkeypatch, tmp_path):
    assert not _quiet_main(monkeypatch, tmp_path, fingerprint="abc", last={"ok": True, "fingerprint": "abc"})


def test_if_changed_runs_after_any_change(monkeypatch, tmp_path):
    assert _quiet_main(monkeypatch, tmp_path, fingerprint="def", last={"ok": True, "fingerprint": "abc"})


def test_if_changed_never_trusts_a_failed_run(monkeypatch, tmp_path):
    """A failing morning must re-check the next one, even on an unchanged checkout."""
    assert _quiet_main(monkeypatch, tmp_path, fingerprint="abc", last={"ok": False, "fingerprint": "abc"})


def test_if_changed_runs_when_git_cannot_answer(monkeypatch, tmp_path):
    assert _quiet_main(monkeypatch, tmp_path, fingerprint=None, last={"ok": True, "fingerprint": None})


def test_a_vanished_target_is_an_error(monkeypatch):
    _stub(monkeypatch)
    gone = plugin.Mutant(
        id="gone",
        breaks="x",
        package="gex",
        tests=("t",),
        module="cherrypick.gex.provider",
        attr="NO_SUCH_ATTRIBUTE",
        replacement="always_true",
    )
    report = runner.check([gone])
    assert not report["ok"] and report["mutants"][0]["verdict"] == "error"
