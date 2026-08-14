"""Keyring-backed storage for the notification stack's bearer secrets.

Mostly webhook URLs — a webhook URL is a bearer secret, anyone holding it can post to your channel —
plus the one non-webhook entry (the Lossdog Clerk `__client` cookie, from which lossdog_notifier
mints its short-lived feed tokens). All of it lives in the OS keyring (Windows Credential Manager /
macOS Keychain / Linux Secret Service) alongside the broker credentials, never in config files, env
vars, or logs (the credentials guardrail). One service namespace, one entry per name.

Status/logging never prints a secret itself — only whether an entry is configured.
"""

from __future__ import annotations

import keyring
import keyring.errors

SERVICE_NAME = "cherrypick-notify"
# "discord_follow" is a SECOND Discord webhook, not a second copy of the first: the tastylive Follow
# Feed fires on other people's trades at a cadence nothing else here matches, so it gets its own
# channel to point at its own Discord channel. Keeping it a separate keyring entry (rather than a
# config-supplied URL for one shared webhook) keeps every webhook a keyring-only secret.
# "lossdog" is not a webhook at all: it stores the Clerk __client cookie the Lossdog VIP feed
# notifier mints its 24h API tokens from. It rides this module because the CLI/status plumbing is
# identical and a second keyring namespace would just be a second place to look.
SUPPORTED = ("slack", "discord", "discord_follow", "lossdog")

# Webhook channels keep their historical "<channel>_webhook" entry names — renaming them would
# orphan every stored secret. Non-webhook entries get a name that says what they actually are.
_ENTRIES = {"lossdog": "lossdog_client"}


def _entry(channel: str) -> str:
    return _ENTRIES.get(channel, f"{channel}_webhook")


def get_webhook(channel: str) -> str | None:
    """Return the stored webhook URL for a channel, or None if unset / keyring unavailable."""
    try:
        return keyring.get_password(SERVICE_NAME, _entry(channel))
    except keyring.errors.KeyringError:
        return None


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


def get_lossdog_client() -> str | None:
    """The Clerk __client cookie for the Lossdog feed, or None. A named accessor so the notifier's
    call site says what it is fetching — `get_webhook("lossdog")` would work and mislead."""
    return get_webhook("lossdog")


def is_set(channel: str) -> bool:
    return bool(get_webhook(channel))


def status(channels=SUPPORTED) -> dict[str, str]:
    """A loggable, secret-free view: {channel: 'set' | 'not set'}."""
    return {ch: ("set" if is_set(ch) else "not set") for ch in channels}
