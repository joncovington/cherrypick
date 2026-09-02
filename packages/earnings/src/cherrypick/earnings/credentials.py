"""Keyring-backed credential storage for tastytrade OAuth.

Thin shim over cherrypick-core's shared ``CredentialStore`` (see ``cherrypick.core.auth``). The keyring logic
now lives in the shared core so all suite modules behave identically; this module only supplies
EarningsAgent's parameters and re-exports the module-level API existing call sites already import.
EarningsAgent uses the ``earningsagent`` keyring service with no legacy fallback.
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
)

SERVICE_NAME = "earningsagent"

# The single store instance for this module; session.py builds its SessionManager from it.
# Own service first (the override/rotation layer), then the suite-wide shared login
# (cherrypick-broker; entered once via the onboarding wizard).
store = CredentialStore(SERVICE_NAME, legacy_service_names=(SHARED_SERVICE,))

get_secret = store.get_secret
set_secret = store.set_secret
delete_secret = store.delete_secret
secrets_present = store.secrets_present
missing_secrets = store.missing_secrets
secrets_status = store.secrets_status

__all__ = [
    "CredentialError",
    "CredentialStore",
    "store",
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
]
