"""Keyring-backed credential storage for tastytrade OAuth.

Thin shim over cherrypick-core's shared ``CredentialStore`` (see ``cherrypick.core.auth``). The keyring logic
now lives in the shared core so all suite modules behave identically; this module only supplies MEIC's
parameters and re-exports the module-level API existing call sites already import, so nothing else
changes. MEIC keeps its read-only fallback to the pre-rename ``tastytrade-mcp`` service so credentials
stored before the rename keep working (never written to going forward).
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

SERVICE_NAME = "meicagent"
_LEGACY_SERVICE_NAME = "tastytrade-mcp"  # read-only fallback for pre-rename credentials

# The single store instance for this module; session.py builds its SessionManager from it.
# Read-through order: own service (the override/rotation layer) -> pre-rename legacy ->
# the suite-wide shared login (cherrypick-broker; entered once via the onboarding wizard).
store = CredentialStore(SERVICE_NAME, legacy_service_names=(_LEGACY_SERVICE_NAME, SHARED_SERVICE))

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
