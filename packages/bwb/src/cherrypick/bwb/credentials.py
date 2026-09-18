"""bwb broker credentials -- OS keyring only, service `bwbagent`, used by the LIVE loop alone.

The paper loop holds no credentials and never imports this module. The store/session pattern is
the suite's (`cherrypick.core.auth`): OAuth secrets live in the OS keyring, never in files, env or
logs; the orchestrator's `connect`/`account` onboarding drives `broker_cli.py`'s hidden-input
commands (`keyring_service: "bwbagent"` on the orchestrator side). Read-through order: this
module's own service (the override/rotation layer), then the suite-wide shared login
(`cherrypick-broker`, entered once) -- so a box with the shared login needs no new secret.
"""

from __future__ import annotations

from cherrypick.core.auth import (
    ACCOUNT_NUMBER,
    ALL_SECRETS,
    CLIENT_SECRET,
    REFRESH_TOKEN,
    REQUIRED_SECRETS,
    SHARED_SERVICE,
    CredentialError,
    CredentialStore,
    SessionManager,
)

SERVICE_NAME = "bwbagent"

store = CredentialStore(SERVICE_NAME, legacy_service_names=(SHARED_SERVICE,))
sessions = SessionManager(store)

get_secret = store.get_secret
set_secret = store.set_secret
delete_secret = store.delete_secret
secrets_present = store.secrets_present
missing_secrets = store.missing_secrets
secrets_status = store.secrets_status
designated_account = store.designated_account


def get_session():
    """The cached tastytrade session for this module's keyring credentials."""
    return sessions.get_session()


__all__ = [
    "CredentialError",
    "CredentialStore",
    "store",
    "sessions",
    "SERVICE_NAME",
    "CLIENT_SECRET",
    "REFRESH_TOKEN",
    "ACCOUNT_NUMBER",
    "REQUIRED_SECRETS",
    "ALL_SECRETS",
    "get_secret",
    "set_secret",
    "delete_secret",
    "secrets_present",
    "missing_secrets",
    "secrets_status",
    "designated_account",
    "get_session",
]
