"""The vendor key lives in the keyring only; presence is reported without the value."""

from __future__ import annotations

import json

import keyring
import keyring.errors

from cherrypick.backtest import __main__ as cli
from cherrypick.backtest import credential, settings

S = settings.resolve({})
ENTRY = ("cherrypick-thetadata", "production:api_key")


def test_absent_then_set_then_present(mem_keyring):
    assert credential.key_present(S) is False
    assert credential.set_key(S, prompt_fn=lambda _p: "  k3y-value \n") is True
    assert mem_keyring[ENTRY] == "k3y-value", "pasted whitespace is not part of the key"
    assert credential.key_present(S) is True


def test_a_blank_entry_keeps_the_current_key(mem_keyring):
    mem_keyring[ENTRY] = "kept"
    assert credential.set_key(S, prompt_fn=lambda _p: "") is False
    assert mem_keyring[ENTRY] == "kept"


def test_an_unreadable_keyring_is_unknown_not_absent(monkeypatch):
    def boom(*_a):
        raise keyring.errors.NoKeyringError("no backend")

    monkeypatch.setattr(keyring, "get_password", boom)
    assert credential.key_present(S) is None


def test_import_env_copies_once_and_says_to_remove_the_variable(mem_keyring):
    res = credential.import_env(S, environ={"THETADATA_API_KEY": "from-env"})
    assert res["ok"] and mem_keyring[ENTRY] == "from-env"
    assert "remove THETADATA_API_KEY" in res["next"]


def test_import_env_without_the_variable_refuses(mem_keyring):
    res = credential.import_env(S, environ={})
    assert res == {"ok": False, "reason": "THETADATA_API_KEY is not set in this process's environment"}
    assert ENTRY not in mem_keyring


def test_key_status_never_prints_the_value(mem_keyring, capsys):
    mem_keyring[ENTRY] = "super-secret-value"
    assert cli.main(["key", "status"]) == 0
    out = capsys.readouterr().out
    assert "super-secret-value" not in out
    assert json.loads(out)["present"] is True


def test_status_reports_presence_but_not_the_value(mem_keyring, capsys):
    mem_keyring[ENTRY] = "super-secret-value"
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "super-secret-value" not in out
    report = json.loads(out)
    assert report["key_present"] is True and report["enabled"] is False
