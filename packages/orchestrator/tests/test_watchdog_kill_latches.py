"""B1: a live arm its kill criteria blocked is CRITICAL until a person clears the latch."""

from __future__ import annotations

from cherrypick.core import killcriteria as kc

from cherrypick.orchestrator import watchdog as wd


def test_a_latched_arm_is_critical_and_clearing_ends_it(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    assert wd._check_kill_latches() == []
    kc.latch("bwb", "control", {"state": "blocked", "reasons": ["drawdown 9100.00 >= 9000.00"]})
    [f] = wd._check_kill_latches()
    assert f.status == wd.CRITICAL and f.key == "kill.bwb"
    assert "control" in f.title and "9100.00" in f.message and "clear --module bwb" in f.message
    kc.clear("bwb")
    assert wd._check_kill_latches() == []
