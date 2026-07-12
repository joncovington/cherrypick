"""Guided per-module onboarding (orchestrator.connect).

Unit lane: asserts the bearer-secret step is DELEGATED to the module's own tool (launched with the tty
inherited — never captured, so the orchestrator never sees client_secret / refresh_token) and that the
account step drives accounts.set_account with the user's selection.
"""

import types

import pytest

from cherrypick.orchestrator import accounts, connect
from cherrypick.orchestrator import config as cfgmod

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "meic").mkdir()
    monkeypatch.setattr(cfgmod, "ROOT", tmp_path)
    cfg = {"modules": {"meic": {"enabled": True, "path": str(tmp_path / "meic"), "keyring_service": "svc"}}}
    return tmp_path, cfg


def test_connect_delegates_secrets_and_sets_account(env, monkeypatch):
    _, cfg = env
    calls = {}

    def fake_subprocess_run(argv, cwd=None, **kwargs):
        calls["argv"] = argv
        calls["cwd"] = cwd
        calls["kwargs"] = kwargs
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(connect.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(
        connect.doctor,
        "_run",
        lambda root, argv, timeout=30: types.SimpleNamespace(
            stdout='{"ok": true, "account_count": 2}', stderr="", returncode=0
        ),
    )
    monkeypatch.setattr(
        accounts,
        "list_accounts",
        lambda cfg, module: {
            "ok": True,
            "module": module,
            "accounts": [
                {"account": "****4222", "nickname": None, "type": "Individual", "designated": False}
            ],
            "designated": None,
            "live_enabled": True,
        },
    )
    set_calls = {}

    def fake_set(cfg, module, sel):
        set_calls["sel"] = sel
        return {"ok": True, "designated": "****4222"}

    monkeypatch.setattr(accounts, "set_account", fake_set)

    out = connect.run(cfg, "meic", prompt_fn=lambda _p: "1")
    assert out["ok"] is True and out["connected"] is True and out["account"] == "****4222"
    # bearer-secret step: the module's own tool, tty inherited (NOT captured)
    assert calls["argv"][1:] == ["src/tt.py", "secrets_set", "--keys", "client_secret", "refresh_token"]
    assert "capture_output" not in calls["kwargs"] and "stdout" not in calls["kwargs"]
    # account step drove set_account with the chosen index
    assert set_calls["sel"] == "1"


def test_connect_secrets_failure_aborts(env, monkeypatch):
    _, cfg = env
    monkeypatch.setattr(connect.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=1))
    out = connect.run(cfg, "meic", prompt_fn=lambda _p: "")
    assert out["ok"] is False and out["step"] == "secrets_set"


def test_connect_skip_account_leaves_unchanged(env, monkeypatch):
    _, cfg = env
    monkeypatch.setattr(connect.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0))
    monkeypatch.setattr(
        connect.doctor,
        "_run",
        lambda root, argv, timeout=30: types.SimpleNamespace(stdout='{"ok": true}', stderr="", returncode=0),
    )
    monkeypatch.setattr(
        accounts,
        "list_accounts",
        lambda cfg, module: {
            "ok": True,
            "module": module,
            "accounts": [{"account": "****4222"}],
            "designated": "****4222",
            "live_enabled": False,
        },
    )
    called = {"set": False}
    monkeypatch.setattr(accounts, "set_account", lambda *a, **k: called.update(set=True))
    # empty selection -> skip, set_account never called
    out = connect.run(cfg, "meic", prompt_fn=lambda _p: "")
    assert out["ok"] is True and called["set"] is False
