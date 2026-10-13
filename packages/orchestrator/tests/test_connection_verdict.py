"""How `doctor` and `connect` read a module's `get_connection_status`.

The payloads are produced by the real `cherrypick.core.broker.cli` functions both CLI shapes call
(broker_cli: bwb, flies; tt.py: meic, earnings), so a change to what a module says is what these
tests read, not a hand-kept copy of it.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from cherrypick.core.broker import cli as broker_cli_core

from cherrypick.orchestrator import connect, doctor
from cherrypick.orchestrator.doctor import FAIL

HINT = "run `cherrypick connect --module bwb` to store the OAuth secrets"


def _creds(present: bool):
    store = SimpleNamespace(
        secrets_present=lambda: present, secrets_status=lambda: {"client_secret": present}
    )
    return SimpleNamespace(
        SERVICE_NAME="bwbagent", store=store, get_session=object, designated_account=lambda: None
    )


def _no_credentials_broker_cli() -> str:
    out = asyncio.run(broker_cli_core.connection_status(_creds(False), live_trading_enabled=False, hint=HINT))
    return json.dumps(out)


def _no_credentials_tt() -> str:
    out = asyncio.run(
        broker_cli_core.connection_check(
            object, credentials_present=False, live_trading_enabled=False, hint=HINT
        )
    )
    return json.dumps(out)


def test_no_credentials_is_not_connected_and_says_what_to_do_in_both_shapes():
    for stdout in (_no_credentials_broker_cli(), _no_credentials_tt()):
        ok, detail = doctor.connection_verdict(stdout)
        assert ok is False, stdout
        assert detail.startswith("no credentials stored") and HINT in detail


def test_an_old_module_saying_ok_without_credentials_is_still_not_connected():
    """bwb before 2026-10-12: `ok: True`, `credentials_present: false`."""
    ok, detail = doctor.connection_verdict(
        json.dumps({"ok": True, "credentials_present": False, "hint": HINT})
    )
    assert ok is False and HINT in detail


def test_connected_and_failed_sessions():
    assert doctor.connection_verdict(json.dumps({"ok": True, "connected": True, "account_count": 2})) == (
        True,
        "connected (2 account(s))",
    )
    ok, detail = doctor.connection_verdict(
        json.dumps({"ok": False, "connected": False, "error": "RuntimeError: 401"})
    )
    assert ok is False and detail == "not connected: RuntimeError: 401"
    assert doctor.connection_verdict("")[0] is False


def test_doctor_reads_the_hint_from_a_nonzero_exit(tmp_path, monkeypatch):
    """The module exits 1 on `ok: false`; doctor used to discard stdout on any non-zero exit and print
    160 raw characters, which the secrets block filled before the hint."""
    module = tmp_path / "bwb"
    module.mkdir()
    (module / "config.json").write_text("{}", encoding="utf-8")
    stdout = _no_credentials_broker_cli()
    monkeypatch.setattr(
        doctor, "_run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=stdout, stderr="")
    )
    cfg = {
        "timezone": "America/New_York",
        "modules": {"bwb": {"enabled": True, "path": str(module), "paper": {}}},
        "notify": {"channels": ["log"]},
    }
    check = next(c for c in doctor.run(cfg) if c.name == "broker/keyring")
    assert check.status == FAIL and HINT in check.detail


def test_connect_prints_not_connected_with_the_hint(monkeypatch, capsys, tmp_path):
    stdout = _no_credentials_broker_cli()
    monkeypatch.setattr(
        doctor, "_run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=stdout, stderr="")
    )
    out = connect._verify_connection(tmp_path, ["-m", "cherrypick.bwb.broker_cli"])
    assert out == {"connected": False, "account_count": None}
    printed = capsys.readouterr().out
    assert "NOT connected" in printed and HINT in printed


def test_connect_reports_a_good_connection(monkeypatch, tmp_path):
    stdout = json.dumps({"ok": True, "connected": True, "account_count": 3})
    monkeypatch.setattr(
        doctor, "_run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=stdout, stderr="")
    )
    assert connect._verify_connection(tmp_path, ["x"]) == {"connected": True, "account_count": 3}
