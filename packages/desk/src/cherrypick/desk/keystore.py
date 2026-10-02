"""The desk's own keyring entries — the PIN verifier, its failure counter, and the ticket HMAC key.

One small seam so every secret the desk keeps goes through the same place, and so the tests replace
exactly one function (`_keyring`) to keep off the real OS credential store. Broker credentials are
NOT here: the desk borrows those from a module's service (see `session.py`) and stores none.

Every call raises `KeystoreError` on a backend failure rather than returning a default. Callers
decide what a failure means, and in this package it always means "refuse".
"""

from __future__ import annotations

from .config import KEYRING_SERVICE


class KeystoreError(RuntimeError):
    """The OS keyring could not be read or written."""


def _keyring():
    import keyring  # imported lazily so the pure layers test without a keyring backend

    return keyring


def get(key: str, *, service: str = KEYRING_SERVICE) -> str | None:
    try:
        return _keyring().get_password(service, key)
    except Exception as exc:  # noqa: BLE001 — any backend failure is a refusal upstream
        raise KeystoreError(f"keyring read failed for {key!r}: {type(exc).__name__}") from exc


def put(key: str, value: str, *, service: str = KEYRING_SERVICE) -> None:
    try:
        _keyring().set_password(service, key, value)
    except Exception as exc:  # noqa: BLE001
        raise KeystoreError(f"keyring write failed for {key!r}: {type(exc).__name__}") from exc


def delete(key: str, *, service: str = KEYRING_SERVICE) -> None:
    """Remove an entry. An entry that is already absent is the desired end state, not an error."""
    try:
        if _keyring().get_password(service, key) is None:
            return
        _keyring().delete_password(service, key)
    except Exception as exc:  # noqa: BLE001
        raise KeystoreError(f"keyring delete failed for {key!r}: {type(exc).__name__}") from exc
