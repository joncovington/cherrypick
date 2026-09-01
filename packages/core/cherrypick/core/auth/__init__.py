"""cherrypick.core.auth — keyring credentials + lazy OAuth session (parameterized for each consumer)."""

from .credentials import (
    ACCOUNT_NUMBER,
    ALL_SECRETS,
    CLIENT_SECRET,
    REFRESH_TOKEN,
    REQUIRED_SECRETS,
    SHARED_SERVICE,
    CredentialError,
    CredentialStore,
    prompt_and_store,
)
from .session import SessionFactory, SessionManager

__all__ = [
    "CredentialStore",
    "prompt_and_store",
    "CredentialError",
    "SessionManager",
    "SessionFactory",
    "CLIENT_SECRET",
    "REFRESH_TOKEN",
    "ACCOUNT_NUMBER",
    "REQUIRED_SECRETS",
    "ALL_SECRETS",
    "SHARED_SERVICE",
]
