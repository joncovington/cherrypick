"""`tt --login NAME`: account reads on an extra broker login, and nothing else.

The guard that matters: an order verb on an extra login refuses before any broker call. No module
trades an extra login, and an order path that reached the broker there would act on an account no
designation, kill criteria or orphan sweep covers.
"""

import json
import sys

import pytest
from cherrypick.core.auth import CLIENT_SECRET, SHARED_SERVICE, CredentialStore, logins

from cherrypick.meic import tt


@pytest.fixture
def mem_keyring(monkeypatch):
    import keyring

    store: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(keyring, "get_password", lambda s, e: store.get((s, e)))
    monkeypatch.setattr(keyring, "set_password", lambda s, e, v: store.__setitem__((s, e), v))
    return store


@pytest.fixture
def restore_tt(monkeypatch):
    # _use_login rebinds module globals; put them back after each test.
    monkeypatch.setattr(tt, "_creds", tt._creds)
    monkeypatch.setattr(tt, "get_session", tt.get_session)


def _run(monkeypatch, capsys, argv):
    monkeypatch.setattr(sys, "argv", ["tt", *argv])
    code = 0
    try:
        tt.main()
    except SystemExit as exc:
        code = exc.code or 0
    lines = capsys.readouterr().out.strip().splitlines()
    return code, json.loads(lines[-1])


@pytest.mark.parametrize(
    "verb,extra",
    [
        ("execute_trade", ["--order", "{}", "--live"]),
        ("adjust_order", ["--order_id", "1", "--order", "{}", "--live"]),
        ("close_position", ["--order_id", "1"]),
        ("get_quote", ["--symbol", "SPX"]),
    ],
)
def test_non_read_verbs_refuse_an_extra_login_before_any_broker_call(
    monkeypatch, capsys, restore_tt, verb, extra
):
    called = []

    async def handler(args):
        called.append(verb)
        return {"ok": True}

    monkeypatch.setattr(tt, f"cmd_{verb}", handler)
    monkeypatch.setattr(tt, "get_session", lambda: called.append("session"))
    code, out = _run(monkeypatch, capsys, ["--login", "margin", verb, *extra])
    assert code == 1
    assert out["ok"] is False and out["reason"] == "login_read_only"
    assert called == []


def test_a_read_verb_runs_on_the_extra_logins_own_store(monkeypatch, capsys, restore_tt, mem_keyring):
    CredentialStore(SHARED_SERVICE).set_secret(CLIENT_SECRET, "main-secret")
    logins.login_store("margin").set_secret(CLIENT_SECRET, "margin-secret")
    seen = {}

    async def handler(args):
        seen["secret"] = tt._creds.get_secret(CLIENT_SECRET)
        seen["account_default"] = tt._creds.get_secret(tt._creds.ACCOUNT_NUMBER)
        # (conftest stubs `_designated_account` itself; the swapped store is what it reads for real)
        seen["designated"] = tt._creds.store.designated_account()
        return {"ok": True, "accounts": []}

    monkeypatch.setattr(tt, "cmd_list_accounts", handler)
    code, out = _run(monkeypatch, capsys, ["--login", "margin", "list_accounts"])
    assert code == 0 and out["ok"] is True
    assert seen == {"secret": "margin-secret", "account_default": None, "designated": None}


def test_no_login_and_main_are_todays_behaviour(monkeypatch, capsys, restore_tt):
    async def handler(args):
        return {"ok": True, "placed": True}

    monkeypatch.setattr(tt, "cmd_execute_trade", handler)
    before = tt._creds
    for argv in (["execute_trade", "--order", "{}"], ["--login", "main", "execute_trade", "--order", "{}"]):
        code, out = _run(monkeypatch, capsys, argv)
        assert code == 0 and out == {"ok": True, "placed": True}
        assert tt._creds is before


def test_a_bad_login_name_is_refused(monkeypatch, capsys, restore_tt):
    code, out = _run(monkeypatch, capsys, ["--login", "../x", "list_accounts"])
    assert code == 1 and "not a login name" in out["error"]


def test_the_read_verbs_are_exactly_the_account_reads():
    assert tt.LOGIN_READ_VERBS == {
        "get_connection_status",
        "list_accounts",
        "get_account_info",
        "get_positions",
        "get_transactions",
        "get_working_orders",
    }
