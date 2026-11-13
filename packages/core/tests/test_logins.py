"""Named broker logins (`cherrypick.core.auth.logins`) and the auth CLI's `--login`.

The rule worth a test: an extra login's store never reads another login's secrets. A store that fell
back to `main` would show main's accounts under the other login's name.
"""

import argparse

import pytest

from cherrypick.core.auth import CLIENT_SECRET, REFRESH_TOKEN, SHARED_SERVICE, CredentialStore, logins
from cherrypick.core.auth import __main__ as cli


def _args(**kw):
    return argparse.Namespace(**kw)


def test_main_is_the_shared_service_unchanged():
    assert logins.login_service(None) == SHARED_SERVICE
    assert logins.login_service("") == SHARED_SERVICE
    assert logins.login_service("main") == SHARED_SERVICE
    assert logins.login_store("main").legacy_service_names == ()


def test_extra_login_has_its_own_service():
    assert logins.login_service("margin") == f"{SHARED_SERVICE}.margin"


@pytest.mark.parametrize("bad", ["Margin", "a b", "../x", "-x", "x" * 33, "a.b", "a_b"])
def test_bad_login_names_are_refused(bad):
    assert not logins.valid_login_name(bad)
    with pytest.raises(logins.LoginNameError):
        logins.login_service(bad)


@pytest.mark.parametrize("good", ["margin", "ira2", "reg-t", "a"])
def test_good_login_names(good):
    assert logins.valid_login_name(good)


def test_extra_login_never_reads_main(mem_keyring):
    CredentialStore(SHARED_SERVICE).set_secret(CLIENT_SECRET, "main-secret")
    extra = logins.login_store("margin")
    assert extra.get_secret(CLIENT_SECRET) is None
    assert extra.secrets_present() is False
    extra.set_secret(CLIENT_SECRET, "margin-secret")
    # And main never reads the extra one.
    assert logins.login_store("main").get_secret(CLIENT_SECRET) == "main-secret"


def test_setup_and_status_per_login(mem_keyring, monkeypatch):
    values = iter(["m-secret", "m-token"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(values))
    out = cli.cmd_setup(_args(keys=[CLIENT_SECRET, REFRESH_TOKEN], login="margin"))
    assert out["ok"] and out["login"] == "margin" and out["service"] == f"{SHARED_SERVICE}.margin"
    assert cli.cmd_status(_args(login="margin"))["secrets"][REFRESH_TOKEN] is True
    # main untouched
    assert cli.cmd_status(_args(login=None))["secrets"][CLIENT_SECRET] is False


def test_remove_deletes_an_extra_login_and_refuses_main(mem_keyring):
    logins.login_store("margin").set_secret(CLIENT_SECRET, "x")
    CredentialStore(SHARED_SERVICE).set_secret(CLIENT_SECRET, "keep")
    out = cli.cmd_remove(_args(login="margin"))
    assert out["ok"] and out["secrets"][CLIENT_SECRET] is False
    refused = cli.cmd_remove(_args(login="main"))
    assert refused["ok"] is False
    assert CredentialStore(SHARED_SERVICE).get_secret(CLIENT_SECRET) == "keep"


def test_cli_rejects_a_bad_login_name(mem_keyring, capsys):
    assert cli.main(["status", "--login", "Bad Name"]) == 1
    assert "not a login name" in capsys.readouterr().out
