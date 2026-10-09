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
# Push channels whose secret is a single webhook URL. TELEGRAM is a push channel too but stores TWO
# entries (a bot token and a chat ID), so it is deliberately absent here: `status()` and the settings
# page render one URL field per name in this tuple, and a URL is not what a Telegram bot takes.
SUPPORTED = ("slack", "discord")
PUSH_CHANNELS = SUPPORTED + ("telegram",)
# Webhooks one job posts to and nothing else: never a push channel, so none can be listed in
# `notify.channels` (the notifier skips an unknown name; `doctor` warns), and no suite alert can
# land in them. `discord_reporting` is the reporting channel the QuikOptions series posts to
# (docs/quikoptions-plan.md).
DEDICATED = ("discord_reporting",)
WEBHOOKS = SUPPORTED + DEDICATED


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
    # Telegram has no webhook URL — `set_telegram` stores its two entries. Raising (rather than
    # silently writing a `telegram_webhook` nothing reads) keeps every generic set path honest.
    if channel == "telegram":
        raise ValueError("telegram takes a bot token and chat ID (cherrypick secrets-set --channel telegram)")
    keyring.set_password(SERVICE_NAME, _entry(channel), url)


def delete_webhook(channel: str) -> bool:
    if channel == "telegram":
        return delete_telegram()
    try:
        keyring.delete_password(SERVICE_NAME, _entry(channel))
        return True
    except keyring.errors.PasswordDeleteError:
        return False
    except keyring.errors.KeyringError:
        return False


def get_telegram_token() -> str | None:
    """The Telegram bot token, or None if unset / keyring unavailable."""
    try:
        value = keyring.get_password(SERVICE_NAME, "telegram_token")
    except keyring.errors.KeyringError:
        value = KEYRING_UNAVAILABLE
    return None if value is KEYRING_UNAVAILABLE else value


def get_telegram_chat_id() -> str | None:
    """The Telegram chat ID to post to, or None if unset / keyring unavailable."""
    try:
        value = keyring.get_password(SERVICE_NAME, "telegram_chat_id")
    except keyring.errors.KeyringError:
        value = KEYRING_UNAVAILABLE
    return None if value is KEYRING_UNAVAILABLE else value


def set_telegram(token: str, chat_id: str) -> None:
    keyring.set_password(SERVICE_NAME, "telegram_token", token)
    keyring.set_password(SERVICE_NAME, "telegram_chat_id", chat_id)


def delete_telegram() -> bool:
    ok = True
    for entry in ("telegram_token", "telegram_chat_id"):
        try:
            keyring.delete_password(SERVICE_NAME, entry)
        except keyring.errors.PasswordDeleteError:
            pass
        except keyring.errors.KeyringError:
            ok = False
    return ok


def is_set(channel: str) -> bool:
    """Whether a channel can post. Telegram is set only when BOTH of its entries are stored —
    `get_webhook` cannot answer this, because a Telegram bot has no webhook URL."""
    if channel == "telegram":
        return bool(get_telegram_token()) and bool(get_telegram_chat_id())
    return bool(get_webhook(channel))


def status(channels=PUSH_CHANNELS + DEDICATED) -> dict[str, str]:
    """A loggable, secret-free view: {channel: 'set' | 'not set'}."""
    return {ch: ("set" if is_set(ch) else "not set") for ch in channels}
