"""Embedded module dashboards (orchestrator.embeds).

Unit lane: config selection, PAPER-mode launch/regeneration driven by config-declared argv, and the
throttle on static regen — all with the module subprocess/socket stubbed so no real module checkout,
server, or broker is needed.
"""

import pytest

from cherrypick.orchestrator import embeds

pytestmark = pytest.mark.unit


def test_enabled_embeds_and_by_id():
    assert embeds.enabled_embeds({}) == []
    cfg = {
        "dashboard": {
            "embeds": [
                {"id": "meic", "enabled": True},
                {"id": "earnings", "enabled": False},
                {"enabled": True},  # no id -> ignored
            ]
        }
    }
    assert [e["id"] for e in embeds.enabled_embeds(cfg)] == ["meic"]
    assert embeds.by_id(cfg, "meic")["id"] == "meic"
    assert embeds.by_id(cfg, "earnings") is None


def test_subst_fills_port_token():
    argv = embeds._subst(["src/dashboard.py", "--port", "{port}"], {"port": 8801})
    assert argv == ["src/dashboard.py", "--port", "8801"]


def test_ensure_server_already_up_does_not_launch(monkeypatch):
    monkeypatch.setattr(embeds, "_port_reachable", lambda h, p: True)

    def fail(*a, **k):
        raise AssertionError("must not launch when the port is already up")

    monkeypatch.setattr(embeds, "_launch_detached", fail)
    res = embeds.ensure_server({"id": "meic", "port": 8801})
    assert res["ok"] is True and res["running"] is True
    assert res["url"] == "http://127.0.0.1:8801/"


def test_recycle_servers_targets_only_reachable_server_embeds(monkeypatch):
    """At serve startup, recycle only 'server' embeds with something actually on the port (a prior
    session's orphan). Static embeds and down ports are left alone."""
    killed = []
    monkeypatch.setattr(embeds, "_port_reachable", lambda h, p: p in (8801, 8802))
    monkeypatch.setattr(embeds, "_recycle_port", lambda h, p: (killed.append(p) or True))
    cfg = {"dashboard": {"embeds": [
        {"id": "meic", "enabled": True, "kind": "server", "port": 8801},
        {"id": "gex", "enabled": True, "kind": "server", "port": 8802},
        {"id": "earnings", "enabled": True, "kind": "static"},        # static -> never recycled
        {"id": "off", "enabled": False, "kind": "server", "port": 9999},  # disabled -> skipped
    ]}}
    assert sorted(embeds.recycle_servers(cfg)) == ["gex", "meic"]
    assert sorted(killed) == [8801, 8802]


def test_recycle_servers_skips_ports_with_nothing_listening(monkeypatch):
    killed = []
    monkeypatch.setattr(embeds, "_port_reachable", lambda h, p: False)
    monkeypatch.setattr(embeds, "_recycle_port", lambda h, p: (killed.append(p) or True))
    cfg = {"dashboard": {"embeds": [{"id": "gex", "enabled": True, "kind": "server", "port": 8802}]}}
    assert embeds.recycle_servers(cfg) == []
    assert killed == []  # never kill a port that has nothing on it


def test_ensure_server_launches_when_down(tmp_path, monkeypatch):
    module = tmp_path / "meic"
    module.mkdir()
    calls = {"reach": 0, "launched": None}

    def reachable(h, p):
        calls["reach"] += 1
        return calls["reach"] > 1  # down on the first check, up once launched

    def launch(root, argv):
        calls["launched"] = (str(root), argv)
        return True

    monkeypatch.setattr(embeds, "_port_reachable", reachable)
    monkeypatch.setattr(embeds, "_launch_detached", launch)
    emb = {
        "id": "meic",
        "path": str(module),
        "port": 8801,
        "serve_argv": ["src/dashboard.py", "--mode", "paper", "--port", "{port}"],
    }
    res = embeds.ensure_server(emb, wait_seconds=2)
    assert res["ok"] is True and res["running"] is True
    # launched in the module root, PAPER-mode argv with the port substituted
    assert calls["launched"][0] == str(module)
    assert calls["launched"][1] == ["src/dashboard.py", "--mode", "paper", "--port", "8801"]


def test_ensure_server_missing_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(embeds, "_port_reachable", lambda h, p: False)
    res = embeds.ensure_server({"id": "meic", "path": str(tmp_path / "nope"), "port": 8801})
    assert res["ok"] is False and "not found" in res["detail"]


def test_build_static_runs_generator_and_throttles(tmp_path, monkeypatch):
    embeds._last_build.clear()
    module = tmp_path / "earn"
    (module / "reports").mkdir(parents=True)
    out = module / "reports" / "strategy_dashboard.html"
    runs = {"n": 0}

    def fake_run(argv, cwd, capture_output, text, timeout):
        runs["n"] += 1
        out.write_text("<html>earnings</html>", encoding="utf-8")

        class R:
            returncode = 0
            stdout = "wrote report"
            stderr = ""

        return R()

    monkeypatch.setattr(embeds.subprocess, "run", fake_run)
    monkeypatch.setattr(embeds.cfgmod, "python_exe", lambda: "python")
    emb = {
        "id": "earnings",
        "path": str(module),
        "build_argv": ["src/strategy_dashboard.py", "--mode", "paper"],
        "output": "reports/strategy_dashboard.html",
        "refresh_seconds": 60,
    }
    first = embeds.build_static(emb)
    assert first["ok"] is True and runs["n"] == 1
    # a second call within refresh_seconds is served from the throttle, not re-run
    second = embeds.build_static(emb)
    assert second["ok"] is True and second["detail"] == "cached" and runs["n"] == 1
    assert embeds.read_static(emb) == b"<html>earnings</html>"


def test_build_static_reports_generator_failure(tmp_path, monkeypatch):
    embeds._last_build.clear()
    module = tmp_path / "earn"
    module.mkdir()

    def fake_run(argv, cwd, capture_output, text, timeout):
        class R:
            returncode = 1
            stdout = ""
            stderr = "boom"

        return R()

    monkeypatch.setattr(embeds.subprocess, "run", fake_run)
    monkeypatch.setattr(embeds.cfgmod, "python_exe", lambda: "python")
    emb = {"id": "earnings", "path": str(module), "build_argv": ["x.py"], "output": "out.html"}
    res = embeds.build_static(emb)
    assert res["ok"] is False and "boom" in res["detail"]
    assert embeds.read_static(emb) is None  # nothing written
