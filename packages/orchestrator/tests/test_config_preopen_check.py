"""A config the automations loop would refuse shows before the session opens (#1500).

2026-11-25 to 11-27: a stale group name refused every tick of the loop, which loads its config only
inside the session, and the first alert came 45 minutes after the open. The watchdog now asks the module
(`cli check`, which loads the config as the loop does and writes nothing) on any change to the file and
once a day, and every save of the module's config asks it about the candidate text first.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from cherrypick.core import home as core_home

from cherrypick.orchestrator import automationedit, configedit
from cherrypick.orchestrator import watchdog as wd

pytestmark = pytest.mark.unit

GOOD = '{\n  "groups": {"default": {"enabled": false}}\n}\n'
STALE = '{\n  "groups": {"defualt": {"enabled": false}}\n}\n'
REFUSAL = "arms: groups.defualt: no automation belongs to this group, so it cannot be on"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path / "home"))
    path = core_home.config_path("automations")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(GOOD, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- the watchdog
class Module:
    """Stands in for the module's `cli check`: answers by the file it is pointed at, counting its runs."""

    def __init__(self, path):
        self.path = path
        self.runs = 0
        self.fail = False

    def __call__(self, root, argv, timeout=25):
        self.runs += 1
        assert argv == ["-m", "cherrypick.automations.cli", "check"]

        class R:
            returncode = 1 if self.fail else 0
            stdout = ""

        if not self.fail:
            text = self.path.read_text(encoding="utf-8")
            ok = "defualt" not in text
            R.stdout = json.dumps({"ok": ok, "path": str(self.path), **({} if ok else {"error": REFUSAL})})
        return R()


@pytest.fixture
def module(home, monkeypatch):
    m = Module(home)
    monkeypatch.setattr(wd, "_run_module", m)
    monkeypatch.setattr(wd.cfgmod, "module_root", lambda *a, **k: home.parent)
    return m


EVENING = datetime(2026, 11, 26, 20, 0)  # the evening before the session, long before the loop runs
NEXT_EVENING = datetime(2026, 11, 27, 20, 0)
MCFG = {"paper": {"kind": "self_healing"}}


def check(now=EVENING, mcfg=MCFG):
    return wd._check_config_loads("automations", mcfg, now)


_TICK = [0]


def touch(path, text):
    """Write the file with an mtime that differs from every earlier write's."""
    import os

    path.write_text(text, encoding="utf-8")
    _TICK[0] += 1
    stamp = 1_800_000_000_000_000_000 + _TICK[0] * 1_000_000_000
    os.utime(path, ns=(stamp, stamp))


def test_a_refusing_config_is_a_warning_before_the_open(module, home):
    touch(home, STALE)
    (finding,) = check()
    assert finding.key == "automations.config" and finding.status == wd.WARN
    assert "defualt" in finding.message and "would refuse" in finding.message
    assert str(home.parent) not in finding.message  # no absolute path on an alert


def test_a_loading_config_is_ok(module):
    (finding,) = check()
    assert finding.status == wd.OK


def test_a_fixed_config_clears_the_finding(module, home):
    touch(home, STALE)
    assert check()[0].status == wd.WARN
    touch(home, GOOD)
    assert check()[0].status == wd.OK


def test_the_check_runs_again_only_on_a_change_or_a_new_day(module, home):
    touch(home, STALE)
    for _ in range(4):
        check()
    assert module.runs == 1  # quiet ticks cost two stats, not a subprocess
    check(now=datetime(2026, 11, 26, 23, 59))
    assert module.runs == 1
    touch(home, GOOD)
    check()
    assert module.runs == 2  # the file changed
    check(now=NEXT_EVENING)
    assert module.runs == 3  # a new day: a group start or a dated budget can begin to apply


def test_a_cached_refusal_is_still_reported_each_tick(module, home):
    touch(home, STALE)
    assert [check()[0].status for _ in range(3)] == [wd.WARN] * 3


def test_a_check_that_cannot_run_says_nothing_and_caches_nothing(module, home):
    touch(home, STALE)
    module.fail = True
    assert check() == []
    module.fail = False
    assert check()[0].status == wd.WARN


def test_automations_is_checked_without_the_key_and_off_with_an_empty_one(module):
    assert len(check(mcfg={"paper": {}})) == 1
    assert check(mcfg={"paper": {"config_check_argv": []}}) == []


def test_a_module_that_names_no_check_is_left_alone(module):
    assert wd._check_config_loads("meic", MCFG, EVENING) == []
    assert module.runs == 0


def test_a_watchdog_tick_runs_the_check_on_a_weekend_evening(module, home, monkeypatch, tmp_path):
    """The check is wired into `run`, for any enabled module, with no trading-day or session gate."""
    for attr in [a for a in dir(wd) if a.startswith("_check_") and a != "_check_config_loads"]:
        monkeypatch.setattr(wd, attr, lambda *a, **k: [])
    saturday = datetime(2026, 11, 28, 20, 0)
    monkeypatch.setattr(wd.timeutil, "now_et", lambda tz=None: saturday)
    monkeypatch.setattr(wd.cfgmod, "enabled_modules", lambda cfg: {"automations": MCFG})
    monkeypatch.setattr(wd.cfgmod, "ensure_dirs", lambda: None)
    monkeypatch.setattr(wd, "_HEARTBEAT", tmp_path / "heartbeat.json")
    monkeypatch.setattr(wd, "_log_findings", lambda *a, **k: None)
    monkeypatch.setattr(wd, "_process_notifications", lambda *a, **k: None)
    monkeypatch.setattr(wd, "Notifier", lambda cfg: None)
    touch(home, STALE)
    out = wd.run({})
    row = next(f for f in out["findings"] if f["key"] == "automations.config")
    assert row["status"] == wd.WARN and out["overall"] == wd.WARN


def test_the_example_config_declares_the_check():
    example = json.loads((wd.cfgmod.ROOT / "config.example.json").read_text(encoding="utf-8"))
    assert example["modules"]["automations"]["paper"]["config_check_argv"] == [
        "-m",
        "cherrypick.automations.cli",
        "check",
    ]


# --------------------------------------------------------------------------- the save
CFG = {"modules": {"automations": {"path": "../automations"}}}


@pytest.fixture
def candidates(monkeypatch):
    """The module's check, by the candidate text it is handed."""
    seen: list[str] = []

    def run(cfg, text):
        seen.append(text)
        return {"ok": False, "error": REFUSAL} if "defualt" in text else {"ok": True}

    monkeypatch.setattr(automationedit, "run_candidate_check", run)
    return seen


def test_a_raw_save_the_loop_would_refuse_leaves_the_file_byte_identical(home, candidates):
    before = home.read_bytes()
    out = configedit.apply_raw_save(CFG, "automations", STALE)
    assert out["ok"] is False and out["code"] == "invalid" and REFUSAL in out["error"]
    assert home.read_bytes() == before
    assert not configedit.backup_dir().exists()  # refused before the backup, too


def test_a_field_edit_the_loop_would_refuse_leaves_the_file_byte_identical(home, candidates):
    before = home.read_bytes()
    out = configedit.apply_field_edit(CFG, "automations", "/groups/default", {"enabled": True})
    assert out["ok"] is True  # control: a sound edit goes through
    home.write_bytes(before)
    out = configedit.apply_field_edit(CFG, "automations", "/groups", {"defualt": {"enabled": False}})
    assert out["ok"] is False and REFUSAL in out["error"]
    assert home.read_bytes() == before


def test_a_save_that_fixes_a_broken_config_passes(home, candidates):
    home.write_text(STALE, encoding="utf-8")  # broken on disk
    out = configedit.apply_raw_save(CFG, "automations", GOOD)
    assert out["ok"] is True
    assert home.read_text(encoding="utf-8") == GOOD
    assert candidates == [GOOD]  # the candidate was checked, not the file it replaced


def test_other_targets_and_suites_without_the_module_are_not_asked(home, candidates):
    assert configedit.module_load_refusal({"modules": {}}, "automations", STALE) is None
    assert configedit.module_load_refusal(CFG, "meic", STALE) is None
    assert candidates == []


def test_a_module_that_cannot_be_asked_refuses_the_save(home, monkeypatch):
    monkeypatch.setattr(automationedit, "run_candidate_check", lambda cfg, text: None)
    before = home.read_bytes()
    out = configedit.apply_raw_save(CFG, "automations", GOOD + " ")
    assert out["ok"] is False and out["code"] == "unavailable"
    assert home.read_bytes() == before
