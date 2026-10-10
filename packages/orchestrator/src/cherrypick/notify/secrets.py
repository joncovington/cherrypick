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


def _read(entry: str) -> Any:
    """One keyring entry, raw: the value, None when nothing is stored, KEYRING_UNAVAILABLE when the
    keyring itself failed. Every read in this module goes through here."""
    try:
        return keyring.get_password(SERVICE_NAME, entry)
    except keyring.errors.KeyringError:
        return KEYRING_UNAVAILABLE


def read_entry(channel: str) -> Any:
    """The raw read of a webhook: the stored secret, None when nothing is stored,
    KEYRING_UNAVAILABLE when the keyring itself failed."""
    return _read(_entry(channel))


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
    """True when the secret is gone (deleted, or never stored); False only when the keyring failed
    and the secret may still be there — the one answer a caller must not report as done."""
    if channel == "telegram":
        return delete_telegram()
    try:
        keyring.delete_password(SERVICE_NAME, _entry(channel))
        return True
    except keyring.errors.PasswordDeleteError:
        return True  # nothing stored: already gone, as delete_telegram treats it
    except keyring.errors.KeyringError:
        return False


_TELEGRAM_ENTRIES = ("telegram_token", "telegram_chat_id")


def get_telegram_token() -> str | None:
    """The Telegram bot token, or None if unset / keyring unavailable."""
    value = _read("telegram_token")
    return None if value is KEYRING_UNAVAILABLE else value


def get_telegram_chat_id() -> str | None:
    """The Telegram chat ID to post to, or None if unset / keyring unavailable."""
    value = _read("telegram_chat_id")
    return None if value is KEYRING_UNAVAILABLE else value


def set_telegram(token: str, chat_id: str) -> None:
    keyring.set_password(SERVICE_NAME, "telegram_token", token)
    keyring.set_password(SERVICE_NAME, "telegram_chat_id", chat_id)


def delete_telegram() -> bool:
    ok = True
    for entry in _TELEGRAM_ENTRIES:
        try:
            keyring.delete_password(SERVICE_NAME, entry)
        except keyring.errors.PasswordDeleteError:
            pass
        except keyring.errors.KeyringError:
            ok = False
    return ok


SET, NOT_SET, UNAVAILABLE = "set", "not set", "keyring unavailable"


def state(channel: str) -> str:
    """SET, NOT_SET or UNAVAILABLE. Telegram is SET only when BOTH of its entries are stored, and
    UNAVAILABLE when either read failed — a caller that alarms on NOT_SET must not alarm on an
    outage as if the secret were never configured."""
    entries = _TELEGRAM_ENTRIES if channel == "telegram" else (_entry(channel),)
    values = [_read(e) for e in entries]
    if any(v is KEYRING_UNAVAILABLE for v in values):
        return UNAVAILABLE
    return SET if all(values) else NOT_SET


def is_set(channel: str) -> bool:
    """Whether a channel can post (an unavailable keyring counts as no)."""
    return state(channel) == SET


def status(channels=PUSH_CHANNELS + DEDICATED) -> dict[str, str]:
    """A loggable, secret-free view: {channel: 'set' | 'not set' | 'keyring unavailable'}."""
    return {ch: state(ch) for ch in channels}
