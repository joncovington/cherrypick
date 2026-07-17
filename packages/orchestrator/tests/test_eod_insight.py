"""Tests for the optional Claude-synthesized EOD insight (orchestrator.eod_insight).

No real Claude invocation: the CLI call is behind injectable seams (`_claude_available`, `_run_claude`),
so these assert the gating (opt-in + feature-detected + needs reports), the success write path, and that
the headless invocation forbids execution/edit/network tools.
"""
import types

import pytest

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import eod_insight

pytestmark = pytest.mark.unit

_CFG = {"modules": {"meic": {"enabled": True}, "earnings": {"enabled": True}}}


def _seed_reports(logs_root, day):
    logs_root.mkdir(parents=True, exist_ok=True)
    (logs_root / f"eod-digest-{day}.md").write_text("# digest", encoding="utf-8")
    for mod in ("meic", "earnings"):
        d = logs_root / mod
        d.mkdir()
        (d / f"eod-analysis-{day}.md").write_text(f"# {mod} analysis", encoding="utf-8")
        (d / f"paper-eod-{day}.md").write_text(f"# {mod} metrics", encoding="utf-8")


def test_skipped_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "LOGS_DIR", tmp_path / "logs")
    res = eod_insight.run({"eod_insight": {"enabled": False}, **_CFG}, day="2026-07-16")
    assert res["ok"] is False and res["skipped"] == "disabled"


def test_skipped_when_claude_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(eod_insight, "_claude_available", lambda: None)
    res = eod_insight.run({"eod_insight": {"enabled": True}, **_CFG}, day="2026-07-16")
    assert res["ok"] is False and res["skipped"] == "claude_not_found"


def test_skipped_when_no_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(eod_insight, "_claude_available", lambda: "/usr/bin/claude")
    res = eod_insight.run({"eod_insight": {"enabled": True}, **_CFG}, day="2026-07-16")
    assert res["ok"] is False and res["skipped"] == "no_reports"


def test_success_writes_insight_file(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    _seed_reports(logs, "2026-07-16")
    monkeypatch.setattr(cfgmod, "LOGS_DIR", logs)
    monkeypatch.setattr(eod_insight, "_claude_available", lambda: "/usr/bin/claude")

    captured = {}

    def fake_run(prompt, stdin_text, model, timeout):
        captured["stdin"] = stdin_text
        return {"ok": True, "text": "**Read:** the day went sideways.\n- widen call OTM"}

    monkeypatch.setattr(eod_insight, "_run_claude", fake_run)
    res = eod_insight.run({"eod_insight": {"enabled": True}, **_CFG}, day="2026-07-16")

    assert res["ok"] is True
    # The stdin fed to Claude concatenated all six deterministic files, labelled.
    for label in ("meic eod-analysis", "earnings paper-eod", "suite digest"):
        assert label in captured["stdin"]
    out = (logs / "eod-insight-2026-07-16.md").read_text(encoding="utf-8")
    assert out.startswith("# cherrypick - EOD Insight 2026-07-16")
    assert "not financial advice" in out
    assert "widen call OTM" in out


def test_run_claude_forbids_dangerous_tools(monkeypatch):
    seen = {}

    def fake_subprocess_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return types.SimpleNamespace(returncode=0, stdout="synthesis", stderr="")

    monkeypatch.setattr(eod_insight.subprocess, "run", fake_subprocess_run)
    res = eod_insight._run_claude("prompt", "reports", None, 120)

    assert res == {"ok": True, "text": "synthesis"}
    cmd = seen["cmd"]
    assert cmd[:2] == ["claude", "-p"]
    assert "--output-format" in cmd and "text" in cmd
    assert "--disallowed-tools" in cmd
    for tool in ("Bash", "Edit", "Write", "WebFetch", "WebSearch", "Task"):
        assert tool in cmd
    # Never bypass permissions.
    assert "--dangerously-skip-permissions" not in cmd
    # The pipe is forced to UTF-8 so non-cp1252 report characters (e.g. Δ) don't blow up on Windows.
    assert seen["kwargs"].get("encoding") == "utf-8"
