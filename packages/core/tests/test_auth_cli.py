"""The shared broker-credential CLI (python -m cherrypick.core.auth): setup/status/migrate.

The redesign's structural piece: one shared service, read through by module stores; migrate
consolidates a module service's copies into it (deleting the source — the confirmed
decision) and never silently clobbers a conflicting shared value.
"""

import argparse

from cherrypick.core.auth import (
    ACCOUNT_NUMBER,
    CLIENT_SECRET,
    REFRESH_TOKEN,
    SHARED_SERVICE,
    CredentialStore,
)
from cherrypick.core.auth import __main__ as cli


def _args(**kw):
    return argparse.Namespace(**kw)


def test_module_store_reads_through_the_shared_service(mem_keyring):
    CredentialStore(SHARED_SERVICE).set_secret(CLIENT_SECRET, "shared-secret")
    module = CredentialStore("meicagent", legacy_service_names=("tastytrade-mcp", SHARED_SERVICE))
    assert module.get_secret(CLIENT_SECRET) == "shared-secret"
    # The module's own service still wins when set — the override layer.
    module.set_secret(CLIENT_SECRET, "own-secret")
    assert module.get_secret(CLIENT_SECRET) == "own-secret"
    # And writes never landed in the shared service.
    assert CredentialStore(SHARED_SERVICE).get_secret(CLIENT_SECRET) == "shared-secret"


def test_setup_writes_hidden_input_and_blank_keeps(mem_keyring, monkeypatch):
    values = iter(["s3kret", ""])  # second key left blank
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(values))
    out = cli.cmd_setup(_args(keys=[CLIENT_SECRET, REFRESH_TOKEN]))
    assert out["ok"] and out["service"] == SHARED_SERVICE
    assert out["secrets"][CLIENT_SECRET] is True and out["secrets"][REFRESH_TOKEN] is False


def test_migrate_moves_and_deletes_source(mem_keyring):
    src = CredentialStore("fliesagent")
    src.set_secret(CLIENT_SECRET, "a")
    src.set_secret(REFRESH_TOKEN, "b")
    out = cli.cmd_migrate(_args(from_service="fliesagent", keep_source=False, overwrite=False))
    assert out["ok"] is True
    assert set(out["migrated"]) == {CLIENT_SECRET, REFRESH_TOKEN}
    assert ACCOUNT_NUMBER in out["absent"]
    shared = CredentialStore(SHARED_SERVICE)
    assert shared.get_secret(CLIENT_SECRET) == "a"
    # Source copies are gone — one rotation point remains (the confirmed decision).
    assert src.get_secret(CLIENT_SECRET) is None


def test_migrate_never_silently_clobbers_a_different_shared_value(mem_keyring):
    CredentialStore(SHARED_SERVICE).set_secret(CLIENT_SECRET, "shared-old")
    src = CredentialStore("meicagent")
    src.set_secret(CLIENT_SECRET, "module-new")
    out = cli.cmd_migrate(_args(from_service="meicagent", keep_source=False, overwrite=False))
    assert out["ok"] is False and out["skipped_conflicts"] == [CLIENT_SECRET]
    # Nothing moved, nothing deleted: the conflict is reported for a deliberate --overwrite.
    assert CredentialStore(SHARED_SERVICE).get_secret(CLIENT_SECRET) == "shared-old"
    assert src.get_secret(CLIENT_SECRET) == "module-new"
    out2 = cli.cmd_migrate(_args(from_service="meicagent", keep_source=False, overwrite=True))
    assert out2["ok"] is True
    assert CredentialStore(SHARED_SERVICE).get_secret(CLIENT_SECRET) == "module-new"


def test_migrate_keep_source_copies_without_deleting(mem_keyring):
    src = CredentialStore("earningsagent")
    src.set_secret(REFRESH_TOKEN, "r")
    out = cli.cmd_migrate(_args(from_service="earningsagent", keep_source=True, overwrite=False))
    assert out["ok"] is True and out["deleted_source_copies"] is False
    assert src.get_secret(REFRESH_TOKEN) == "r"
    assert CredentialStore(SHARED_SERVICE).get_secret(REFRESH_TOKEN) == "r"


def test_status_reports_presence_never_values(mem_keyring):
    CredentialStore(SHARED_SERVICE).set_secret(CLIENT_SECRET, "x")
    out = cli.cmd_status(_args())
    assert out["secrets"][CLIENT_SECRET] is True
    assert "x" not in str(out)
