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
    """A rename must fail loudly here, not leave a mutant patching nothing every morning. Skipped
    where the target's package is not installed (this package's own CI job installs core and the
    orchestrator only); CI's `guards` job installs every one, and a vanished target fails it there."""
    pytest.importorskip(mutant.module)
    owner, name = plugin.resolve(mutant)
    assert hasattr(owner, name)


@pytest.mark.parametrize("mutant", plugin.MUTANTS, ids=lambda m: m.id)
def test_every_mutant_names_a_test_that_exists(mutant):
    root = Path(__file__).resolve().parents[2] / mutant.package
    for node in mutant.tests:
        path, _, func = node.partition("::")
        func = func.partition("[")[0]  # a parametrized case names its function before the bracket
        assert (root / path).exists(), node
        assert f"def {func}(" in (root / path).read_text(encoding="utf-8"), node


# A stand-in aimed at this package's own module, so the verdict tests run wherever the orchestrator
# is installed -- a real mutant's target lives in a package this package's CI job does not install.
FAKE = plugin.Mutant(
    id="fake",
    breaks="a stand-in for the verdict tests",
    package="orchestrator",
    tests=("tests/test_util.py::test_clean_json_object",),
    module="cherrypick.orchestrator.util",
    attr="first_json",
    replacement="always_true",
)


def _stub(monkeypatch, *, clean=(0, ""), mutated=(1, "E       AssertionError: assert 1 == 0")):
    def fake(package, tests, mutant_id=None):
        return mutated if mutant_id else clean

    monkeypatch.setattr(runner, "run_pytest", fake)


def test_a_mutant_killed_by_an_assertion_is_ok(monkeypatch):
    _stub(monkeypatch)
    report = runner.check([FAKE])
    assert report["ok"] and report["mutants"][0]["verdict"] == "killed"


def test_a_surviving_mutant_fails_the_check(monkeypatch):
    _stub(monkeypatch, mutated=(0, "1 passed"))
    report = runner.check([FAKE])
    assert not report["ok"] and report["mutants"][0]["verdict"] == "survived"


def test_a_crash_is_not_a_kill(monkeypatch):
    _stub(monkeypatch, mutated=(1, "E   TypeError: unsupported operand"))
    report = runner.check([FAKE])
    assert not report["ok"] and report["mutants"][0]["verdict"] == "error"


def test_a_failing_clean_run_fails_the_check_without_trying_mutants(monkeypatch):
    calls = []

    def fake(package, tests, mutant_id=None):
        calls.append(mutant_id)
        return (1, "E   assert False") if mutant_id is None else (1, "E   AssertionError")

    monkeypatch.setattr(runner, "run_pytest", fake)
    report = runner.check([FAKE])
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
    # The module exists and the attribute does not: a rename, not a package that was never installed.
    gone = plugin.Mutant(
        id="gone",
        breaks="x",
        package="orchestrator",
        tests=("t",),
        module="cherrypick.orchestrator.util",
        attr="NO_SUCH_ATTRIBUTE",
        replacement="always_true",
    )
    report = runner.check([gone])
    assert not report["ok"] and report["mutants"][0]["verdict"] == "error"


FAILED_REPORT = {
    "ok": False,
    "baseline": {},
    "mutants": [{"id": "fake", "verdict": "survived", "breaks": "x", "detail": "1 passed"}],
}


def _run_main(monkeypatch, tmp_path, argv, capsys):
    """Drive main() on a failing verdict with the notifier, the state file and the checkout stubbed;
    return (what the notifier was sent, the state file's path, stderr)."""
    import types

    sent, state = [], tmp_path / "guard-mutants.last.json"

    class Recorder:
        def __init__(self, _cfg=None):
            pass

        def notify(self, *args):
            sent.append(args)

    notifier = types.ModuleType("cherrypick.notify.notifier")
    notifier.Notifier = Recorder
    monkeypatch.setitem(sys.modules, "cherrypick.notify", types.ModuleType("cherrypick.notify"))
    monkeypatch.setitem(sys.modules, "cherrypick.notify.notifier", notifier)
    from cherrypick.orchestrator import config as cfgmod

    monkeypatch.setattr(cfgmod, "load_config", lambda: {})
    monkeypatch.setattr(runner._plugin, "MUTANTS", (FAKE,))
    monkeypatch.setattr(runner, "checkout_fingerprint", lambda: "abc")
    monkeypatch.setattr(runner, "_last_report", lambda: {})
    monkeypatch.setattr(runner, "_state_path", lambda: state)
    monkeypatch.setattr(runner, "check", lambda ms: dict(FAILED_REPORT))
    assert runner.main(argv) == 1
    return sent, state, capsys.readouterr().err


def test_the_scheduled_job_pushes_a_failure_through_the_notifier(monkeypatch, tmp_path, capsys):
    """The one run that tells the owner: `--if-changed`, the supervisor's `guard-mutants` job argv."""
    sent, state, err = _run_main(monkeypatch, tmp_path, ["--if-changed"], capsys)
    assert len(sent) == 1 and sent[0][0] == "WARNING"
    assert "survived" in err and state.exists()


@pytest.mark.parametrize("argv", [[], ["--only", "fake"]], ids=["whole_table_by_hand", "partial_by_hand"])
def test_a_run_by_hand_reports_on_stderr_and_tells_no_one_else(monkeypatch, tmp_path, capsys, argv):
    """Shown to fail by notifying on every failure, as it did: six hand runs reached the owner's chat."""
    sent, _, err = _run_main(monkeypatch, tmp_path, argv, capsys)
    assert sent == []
    assert "WARNING: guard check" in err and "survived" in err


REPO = Path(__file__).resolve().parents[3]


def live_packages() -> set[str]:
    """The packages that can place a live order, found from what the suite declares: a config example
    with a live gate (`enable_live_trading` or `live.enabled`), less any the orchestrator's own
    example marks `_retired` (they ship off and refuse to arm while off). Not a hand-kept list."""
    import json

    modules = json.loads((REPO / "packages/orchestrator/config.example.json").read_text(encoding="utf-8"))[
        "modules"
    ]
    found = set()
    for path in sorted((REPO / "packages").glob("*/config.example.json")):
        package = path.parent.name
        doc = json.loads(path.read_text(encoding="utf-8"))
        live = doc.get("live")
        gated = "enable_live_trading" in doc or (isinstance(live, dict) and "enabled" in live)
        if gated and "_retired" not in (modules.get(package) or {}):
            found.add(package)
    return found


def packages_without_a_mutant(mutants) -> set[str]:
    return live_packages() - {m.package for m in mutants}


def test_every_package_that_can_place_a_live_order_has_a_guard_mutant():
    """A guard on a live-order path adds its mutant to the table in the same PR (working-rules.md); this
    is the floor under that rule: a live package with no mutant at all fails here."""
    assert live_packages() >= {"meic", "ladder"}, "the discovery found nothing: fix it, do not skip it"
    assert not packages_without_a_mutant(plugin.MUTANTS), sorted(packages_without_a_mutant(plugin.MUTANTS))


def test_the_live_package_check_fails_when_a_package_loses_its_mutants():
    """Shown to fire: the table with meic's mutants removed, and with ladder's, names that package."""
    for package in ("meic", "ladder"):
        without = [m for m in plugin.MUTANTS if m.package != package]
        assert packages_without_a_mutant(without) == {package}


def test_a_retired_package_is_exempt_from_the_live_package_check():
    assert not live_packages() & {"flies", "bwb"}


def test_a_partial_run_writes_no_state_file(monkeypatch, tmp_path, capsys):
    """It proves only its own mutants; a record of it would stand in for the whole table's last verdict.
    Shown to fail by writing the record on every run."""
    _, state, _ = _run_main(monkeypatch, tmp_path, ["--only", "fake"], capsys)
    assert not state.exists()


def test_a_whole_table_run_by_hand_still_writes_the_state_file(monkeypatch, tmp_path, capsys):
    _, state, _ = _run_main(monkeypatch, tmp_path, [], capsys)
    assert state.exists()


def test_the_supervisors_job_passes_if_changed():
    """The notification keys on this argument: the job must keep passing it."""
    source = (Path(__file__).resolve().parents[1] / "src/cherrypick/orchestrator/jobspec.py").read_text(
        encoding="utf-8"
    )
    assert '_suite_script(launcher, "guard_mutants.py"), "--if-changed"' in source
