"""The desk PIN — the human checkpoint, held in the OS keyring.

What this is for: the confirmation code in `ticket.py` proves *which order* was ratified, but it is
visible to whatever ran `propose`, so on its own it cannot prove a *person* ratified it. The PIN is
the part that has to come from outside the machine's own state — it is not written to any file, not
echoed, not logged, and not derivable from anything the desk stores.

**Only a verifier is stored, never the PIN.** The keyring holds
`pbkdf2_sha256$<iterations>$<salt>$<hash>`, so reading the keyring entry does not yield the PIN, and
comparison is constant-time. That matters because the keyring is readable by anything running as the
user — the same threat model that makes storing the raw PIN there pointless.

The honest limit, again stated rather than glossed: once a PIN is typed into an agent conversation,
that agent has seen it. The PIN's guarantees are (a) an order cannot be submitted by a process that
has *never* been given it, and (b) non-repudiation in the journal. It is not a defense against an
agent replaying a PIN it was just handed — the order-bound ticket (single-use, expiring, fingerprinted)
and the `policy.py` gates are what constrain that case. Rotate with `set_pin` whenever that matters.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from .config import KEYRING_SERVICE

_PIN_KEY = "confirm_pin_verifier"
_ITERATIONS = 240_000
_MIN_LENGTH = 6


class PinError(RuntimeError):
    """No PIN configured, or one that fails its own format rules."""


def _keyring():
    import keyring  # imported lazily so the pure layers test without a keyring backend

    return keyring


def _derive(pin: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations)


def _format(pin: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _derive(pin, salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${digest.hex()}"


def set_pin(pin: str, *, service: str = KEYRING_SERVICE) -> None:
    """Store (the verifier for) a new PIN. Rejects trivially short PINs.

    No "old PIN" check: the keyring entry is already protected by the OS user session, so anything
    able to overwrite it could equally read the keyring — a confirmation prompt here would be
    ceremony, not security.
    """
    pin = str(pin or "")
    if len(pin.strip()) < _MIN_LENGTH:
        raise PinError(f"PIN must be at least {_MIN_LENGTH} characters")
    _keyring().set_password(service, _PIN_KEY, _format(pin))


def clear_pin(*, service: str = KEYRING_SERVICE) -> None:
    try:
        _keyring().delete_password(service, _PIN_KEY)
    except Exception:  # noqa: BLE001 — absent is the desired end state either way
        pass


def is_set(*, service: str = KEYRING_SERVICE) -> bool:
    try:
        return bool(_keyring().get_password(service, _PIN_KEY))
    except Exception:  # noqa: BLE001 — a broken keyring reads as "not set", which refuses
        return False


def verify(pin: str, *, service: str = KEYRING_SERVICE) -> bool:
    """Constant-time check of a candidate PIN against the stored verifier.

    Returns False (never raises) for an absent or unparseable verifier, so a damaged keyring entry
    refuses orders rather than admitting them.
    """
    try:
        stored = _keyring().get_password(service, _PIN_KEY)
    except Exception:  # noqa: BLE001
        return False
    if not stored:
        return False
    try:
        scheme, iterations, salt_hex, digest_hex = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = _derive(str(pin or ""), bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, actual)


def env_pin() -> str | None:
    """`CHERRYPICK_DESK_PIN`, for a caller that would rather not put the PIN in a command line.

    Deliberately *not* a way to store the PIN: an env var lives only as long as the process that set
    it. The CLI checks this before its `--pin` argument so a shell history never has to hold one.
    """
    value = os.environ.get("CHERRYPICK_DESK_PIN")
    return value if value else None
