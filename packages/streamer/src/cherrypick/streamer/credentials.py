"""Keyring credential entry for the suite's shared tastytrade login.

The streamer OWNS the shared broker login in a standalone (no-MEIC) install, but it needs only the two
OAuth **bearer** secrets — ``client_secret`` and ``refresh_token``. It streams market data and never
makes an account-scoped call, so no ``account_number`` is required here (that is a live-trading concern
belonging to a trading module).

Secrets live in the OS keyring under the ``"meicagent"`` service, with read-only fallbacks to the
pre-rename ``"tastytrade-mcp"`` and the suite's shared broker login (``"cherrypick-broker"``) — the same
chain the module stores use, so storing them under any of the three is interchangeable, and a box that
already has the suite's OAuth (including one migrated into the shared service) needs no re-entry.

``cherrypick connect`` deliberately never sees the bearer secrets itself — it delegates entry to a
module's own tool. This is the streamer's equivalent tool, so a streamer-only box can be onboarded the
same way (see ``docs/streamer-package-plan.md``, Step 0).
"""

from __future__ import annotations

import getpass
from collections.abc import Callable

from cherrypick.core.auth import (
    CLIENT_SECRET,
    REFRESH_TOKEN,
    SHARED_SERVICE,
    CredentialStore,
)

SERVICE = "meicagent"
# Read-only fallbacks, in order: the pre-rename service, then the suite's shared broker login — the
# same chain the module stores use, so credentials migrated into cherrypick-broker keep working here.
LEGACY = ("tastytrade-mcp", SHARED_SERVICE)

# The streamer needs only the bearer secrets — it never makes an account-scoped call.
STREAMER_SECRETS = (CLIENT_SECRET, REFRESH_TOKEN)


def store() -> CredentialStore:
    return CredentialStore(SERVICE, legacy_service_names=LEGACY)


def status() -> dict[str, bool]:
    """{secret: is_present} for the two bearer secrets the streamer needs."""
    creds = store()
    return {key: bool(creds.get_secret(key)) for key in STREAMER_SECRETS}


def set_secrets(
    keys: list[str] | None = None, prompt_fn: Callable[[str], str] = getpass.getpass
) -> list[str]:
    """Prompt (hidden input by default) for each key and store it under the shared service.

    Empty input for a key skips it, leaving any existing value untouched. Returns the keys actually
    written. ``prompt_fn`` is injectable so this is unit-testable without a real terminal.
    """
    creds = store()
    keys = keys or list(STREAMER_SECRETS)
    written: list[str] = []
    for key in keys:
        value = prompt_fn(f"{key}: ")
        if value:
            creds.set_secret(key, value)
            written.append(key)
    return written
