"""Named broker logins: which keyring service holds each tastytrade login's secrets.

A login is a name, never a secret. The suite's first login is `main`, which IS the shared service
(`cherrypick-broker`) every module already reads through, so an install with one login is unchanged.
Each further login keeps its secrets under its own service, `cherrypick-broker.<name>`, with the same
entry names (`production:client_secret`, ...).

An extra login's store has no fallback chain. A module's own service or the shared one can never
answer for it: a store that silently read `main`'s token would show `main`'s accounts under the other
login's name, and an order path built on it would trade the wrong account.

Which logins exist is the caller's to say (the orchestrator reads `broker.logins` from its config);
core only maps a name to its store, so it stays import-self-contained.
"""

from __future__ import annotations

import re

from .credentials import SHARED_SERVICE, CredentialStore

DEFAULT_LOGIN = "main"

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


class LoginNameError(ValueError):
    pass


def valid_login_name(name: str) -> bool:
    """A slug of lower-case letters, digits and hyphens (at most 32), starting with a letter or digit.
    It lands in a keyring service name, file names and job ids, so nothing else is allowed."""
    return isinstance(name, str) and bool(_NAME_RE.match(name))


def check_login_name(name: str | None) -> str:
    """The login name to use: None or "" means `main`; anything not a valid slug raises."""
    if name in (None, ""):
        return DEFAULT_LOGIN
    if not valid_login_name(name):
        raise LoginNameError(f"not a login name: {name!r} (lower-case letters, digits, hyphens)")
    return name


def is_default(name: str | None) -> bool:
    return check_login_name(name) == DEFAULT_LOGIN


def login_service(name: str | None) -> str:
    """The keyring service holding this login's secrets."""
    name = check_login_name(name)
    return SHARED_SERVICE if name == DEFAULT_LOGIN else f"{SHARED_SERVICE}.{name}"


def login_store(name: str | None) -> CredentialStore:
    """A plain store on the login's own service: no legacy or shared fallback."""
    return CredentialStore(login_service(name))
