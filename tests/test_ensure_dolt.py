"""cherrypick-managed Dolt keep-alive: `ensure-dolt` starts a module's declared Dolt server only when
the port is down, and resolves its data_dir portably (no absolute/machine paths in config)."""

import json

import cherrypick.cli as cli
from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import watchdog


def _cfg(data_dir="~/.cherrypick/data/earnings"):
    return {
        "modules": {
            "earnings": {
                "enabled": True,
                "paper": {
                    "dolt_host": "127.0.0.1",
                    "dolt_port": 3306,
                    "dolt_service": {"task_name": "cherrypick-earnings-dolt", "data_dir": data_dir},
                },
            }
        }
    }


def test_data_dir_expands_home():
    from pathlib import Path

    got = cli._dolt_service_dir({"data_dir": "~/somewhere/dolt"})
    assert got == (Path.home() / "somewhere" / "dolt")


def test_data_dir_relative_resolves_against_root():
    got = cli._dolt_service_dir({"data_dir": "data/earnings"})
    assert got == (cfgmod.ROOT / "data" / "earnings").resolve()


def test_ensure_dolt_skips_when_already_up(monkeypatch, capsys):
    monkeypatch.setattr(watchdog, "_dolt_reachable", lambda h, p: True)

    def _must_not_start(_data_dir):  # starting Dolt with the port already up would be a bug
        raise AssertionError("should not start")

    monkeypatch.setattr(cli, "_start_dolt", _must_not_start)
    cli._ensure_dolt(_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["dolt"]["earnings"]["detail"] == "already up"


def test_ensure_dolt_starts_when_down(monkeypatch, capsys):
    monkeypatch.setattr(watchdog, "_dolt_reachable", lambda h, p: False)
    calls = {}

    def fake_start(data_dir):
        calls["dir"] = data_dir
        return True

    monkeypatch.setattr(cli, "_start_dolt", fake_start)
    cli._ensure_dolt(_cfg("~/.cherrypick/data/earnings"))
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert "started in" in out["dolt"]["earnings"]["detail"]
    # data_dir was resolved (home expanded) before starting
    assert str(calls["dir"]).endswith("earnings")


def test_ensure_dolt_reports_failure_when_start_fails(monkeypatch, capsys):
    monkeypatch.setattr(watchdog, "_dolt_reachable", lambda h, p: False)
    monkeypatch.setattr(cli, "_start_dolt", lambda d: False)
    cli._ensure_dolt(_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "start failed" in out["dolt"]["earnings"]["detail"]


def test_ensure_dolt_noop_without_service_block(monkeypatch, capsys):
    cfg = {"modules": {"earnings": {"enabled": True, "paper": {"dolt_host": "127.0.0.1"}}}}
    cli._ensure_dolt(cfg)
    out = json.loads(capsys.readouterr().out)
    assert out == {"ok": True, "dolt": {}}
