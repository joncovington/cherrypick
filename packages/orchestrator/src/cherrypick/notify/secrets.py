"""Keyring-backed storage for notification webhook URLs.

A webhook URL is a bearer secret — anyone holding it can post to your channel — so it lives in the OS
keyring (Windows Credential Manager / macOS Keychain / Linux Secret Service) alongside the broker
credentials, never in config files, env vars, or logs (the credentials guardrail). One service
namespace, one entry per channel.

Status/logging never prints the URL itself — only whether a channel is configured.
"""

from __future__ import annotations

import keyring
import keyring.errors

SERVICE_NAME = "cherrypick-notify"
# "discord_follow" is a SECOND Discord webhook, not a second copy of the first: the tastylive Follow
# Feed fires on other people's trades at a cadence nothing else here matches, so it gets its own
# channel to point at its own Discord channel. Keeping it a separate keyring entry (rather than a
# config-supplied URL for one shared webhook) keeps every webhook a keyring-only secret.
SUPPORTED = ("slack", "discord", "discord_follow")


def _entry(channel: str) -> str:
    return f"{channel}_webhook"


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


def is_set(channel: str) -> bool:
    return bool(get_webhook(channel))


def status(channels=SUPPORTED) -> dict[str, str]:
    """A loggable, secret-free view: {channel: 'set' | 'not set'}."""
    return {ch: ("set" if is_set(ch) else "not set") for ch in channels}
