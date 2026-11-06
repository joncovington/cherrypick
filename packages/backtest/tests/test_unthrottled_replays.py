"""2026-11-05: research ran throttled and half the cores sat idle. Every replay the runner starts is
opted out of power throttling at its spawn, the runner sizes its pool from the machine, and no study
script fixes a worker count of its own."""

from __future__ import annotations

import inspect
import re
import subprocess
from pathlib import Path

from cherrypick.backtest import runner

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def test_each_replay_is_started_unthrottled(monkeypatch, tmp_path):
    calls = []

    def fake(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, '{"ok": true}', "")

    monkeypatch.setattr(runner.core_procs, "run_unthrottled", fake)
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("throttled"))
    )
    cfg = tmp_path / "meic.json"
    assert runner._meic_replay("2024-01-02", tmp_path / "o.db", ["a"], tmp_path / "h.db", 1.0, cfg) == {
        "ok": True
    }
    assert runner._replay("2024-01-02", tmp_path / "o.db", ["a"], tmp_path / "h.db", 1.0, cfg) == {"ok": True}
    assert [c[2] for c in calls] == ["cherrypick.meic.replay", "cherrypick.flies.cli"]


def test_the_runner_sizes_its_pool_from_the_machine():
    assert inspect.signature(runner.run).parameters["workers"].default is None
    assert "core_procs.default_workers()" in inspect.getsource(runner.run)


def test_no_study_script_fixes_its_own_worker_count():
    literal = re.compile(r'add_argument\(\s*"--workers"[^)]*default\s*=\s*\d+')
    offenders = [
        p.name for p in sorted(SCRIPTS.glob("*.py")) if literal.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"use cherrypick.core.procs.default_workers(): {offenders}"
