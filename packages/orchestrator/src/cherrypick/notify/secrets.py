"""Keyring-backed storage for the notification stack's webhook URLs.

A webhook URL is a bearer secret — anyone holding it can post to your channel — so it lives in the
OS keyring (Windows Credential Manager / macOS Keychain / Linux Secret Service) alongside the
broker credentials, never in config files, env vars, or logs (the credentials guardrail). One
service namespace, one entry per name.

The service namespace is shared history: the follow-feed/lossdog notifiers (moved to the
standalone follow-feed-notifier repo 2026-08-21) keep their `discord_follow_webhook` and
`lossdog_client` entries under this same service name, managed by that repo's own CLI now. This
module neither reads nor writes them any more — do not re-add them here, and don't be surprised
to see them beside ours in Credential Manager.

Status/logging never prints a secret itself — only whether an entry is configured.
"""

from __future__ import annotations

from typing import Any

import keyring
import keyring.errors

SERVICE_NAME = "cherrypick-notify"
SUPPORTED = ("slack", "discord")


def _entry(channel: str) -> str:
    # The historical "<channel>_webhook" entry names — renaming them would orphan stored secrets.
    return f"{channel}_webhook"


# Distinct from "nothing stored": the keyring itself refused the read. Windows Credential Manager
# is transiently unavailable often enough to matter (a locked session, a service hiccup), and a
# caller that ALARMS on a missing secret needs the difference — reporting an outage as "you never
# configured this" sends the operator to fix something that is not broken.
KEYRING_UNAVAILABLE = object()


def read_entry(channel: str) -> Any:
    """The raw read: the stored secret, None when nothing is stored, KEYRING_UNAVAILABLE when the
    keyring itself failed."""
    try:
        return keyring.get_password(SERVICE_NAME, _entry(channel))
    except keyring.errors.KeyringError:
        return KEYRING_UNAVAILABLE


def get_webhook(channel: str) -> str | None:
    """Return the stored webhook URL for a channel, or None if unset / keyring unavailable. Callers
    that only need "can I post" keep the simple contract; use read_entry when the difference between
    unset and unavailable changes what you do."""
    value = read_entry(channel)
    return None if value is KEYRING_UNAVAILABLE else value


def set_webhook(channel: str, url: str) -> None:
    keyring.set_password(SERVICE_NAME, _entry(channel), url)


def delete_webhook(channel: str) -> bool:
    try:
        keyring.delete_password(SERVICE_NAME, _entry(channel))
        return True
    except keyring.errors.PasswordDeleteError:
        return False
    except keyring.errors.KeyringError:
        return False


def is_set(channel: str) -> bool:
    return bool(get_webhook(channel))


def status(channels=SUPPORTED) -> dict[str, str]:
    """A loggable, secret-free view: {channel: 'set' | 'not set'}."""
    return {ch: ("set" if is_set(ch) else "not set") for ch in channels}
