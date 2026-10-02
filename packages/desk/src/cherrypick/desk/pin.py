"""The desk PIN — the human checkpoint, held in the OS keyring.

What this is for: the confirmation code in `ticket.py` proves *which order* was ratified, but it is
visible to whatever ran `propose`, so on its own it cannot prove a *person* ratified it. The PIN is
the part that has to come from outside the machine's own state — it is not written to any file, not
echoed, not logged, and not derivable from anything the desk stores.

**Only a verifier is stored, never the PIN.** The keyring holds
`pbkdf2_sha256$<iterations>$<salt>$<hash>`, so reading the keyring entry does not yield the PIN, and
comparison is constant-time. New verifiers use 600,000 iterations; an older record keeps verifying
because the count is read back from the record itself.

**Entry is interactive only.** The PIN is read with `getpass` from an interactive terminal — never a
flag (shell history, process listings), never an environment variable (inherited by every child
process), never a pipe. A non-TTY caller is refused outright.

**Five wrong PINs in an hour lock it for an hour.** The failure counter lives in the keyring next to
the verifier, not in the journal, so deleting a log file does not reset it. A counter that cannot be
read or written refuses the attempt rather than allowing an unlimited one.

**Changing or clearing a PIN needs the current one.** Otherwise anything that can run the CLI could
replace the PIN with one it knows, which would make the PIN decorative.

The honest limit, stated rather than glossed: anything running as this OS user can read and rewrite
the keyring entries directly, and once a PIN is typed into an agent conversation, that agent has seen
it. The PIN stops a process that has *never* been given it and gives non-repudiation in the journal;
it is not a defence against a compromised user account.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sys
import time

from . import keystore

_PIN_KEY = "confirm_pin_verifier"
_FAILURES_KEY = "confirm_pin_failures"

PBKDF2_ITERATIONS = 600_000
MIN_LENGTH = 10
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 3600
LOCKOUT_SECONDS = 3600

# check() outcomes
OK = "ok"
BAD = "bad"  # wrong PIN, counted
LOCKOUT = "lockout"  # wrong PIN, and this one tripped the lockout
LOCKED = "locked"  # already locked out; the candidate was not even compared


class PinError(RuntimeError):
    """No PIN configured, a PIN that fails its own format rules, or a keyring that cannot be used."""


class PinRejected(PinError):
    """A PIN check that did not pass. `outcome` is one of BAD / LOCKOUT / LOCKED."""

    def __init__(self, outcome: str, message: str):
        super().__init__(message)
        self.outcome = outcome


def _derive(pin: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations)


def _format(pin: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _derive(pin, salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


# --------------------------------------------------------------------------- terminal entry
def _isatty() -> bool:
    stdin = sys.stdin
    try:
        return bool(stdin is not None and stdin.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def _getpass(label: str) -> str:
    import getpass

    return getpass.getpass(label)


def prompt(label: str) -> str:
    """Read a PIN without echo from an interactive terminal. Refuses anything else."""
    if not _isatty():
        raise PinError(
            "PIN entry needs an interactive terminal — it is never read from a flag, "
            "the environment or a pipe"
        )
    return _getpass(label)


# --------------------------------------------------------------------------- stored verifier
def _stored() -> str | None:
    try:
        return keystore.get(_PIN_KEY)
    except keystore.KeystoreError as exc:
        raise PinError(str(exc)) from exc


def is_set() -> bool:
    try:
        return bool(_stored())
    except PinError:  # a broken keyring reads as "not set", which refuses
        return False


def _matches(pin: str, stored: str | None) -> bool:
    """Constant-time check against a stored verifier. False (never raises) for an absent or
    unparseable verifier, so a damaged keyring entry refuses orders rather than admitting them."""
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


def verify(pin: str) -> bool:
    """Bare comparison, with no lockout accounting. Order paths use `check`/`require` instead."""
    try:
        return _matches(pin, _stored())
    except PinError:
        return False


# --------------------------------------------------------------------------- lockout
def _failures() -> dict:
    try:
        raw = keystore.get(_FAILURES_KEY)
    except keystore.KeystoreError as exc:
        raise PinError(f"cannot read the PIN failure counter ({exc}) — refusing") from exc
    if not raw:
        return {"failures": [], "locked_until": 0.0}
    try:
        state = json.loads(raw)
        failures = [float(t) for t in state.get("failures") or []]
        locked_until = float(state.get("locked_until") or 0.0)
    except (ValueError, TypeError, AttributeError) as exc:
        raise PinError(
            "the PIN failure counter in the keyring is unreadable — refusing. Delete the "
            f"'{_FAILURES_KEY}' entry under the '{keystore.KEYRING_SERVICE}' keyring service to reset it"
        ) from exc
    return {"failures": failures, "locked_until": locked_until}


def _save_failures(state: dict) -> None:
    try:
        keystore.put(_FAILURES_KEY, json.dumps(state))
    except keystore.KeystoreError as exc:
        raise PinError(f"cannot record a failed PIN attempt ({exc}) — refusing") from exc


def locked_until(now: float | None = None) -> float | None:
    """Epoch seconds the lockout ends, or None when not locked (or the counter is unreadable)."""
    now = time.time() if now is None else now
    try:
        until = _failures()["locked_until"]
    except PinError:
        return None
    return until if until > now else None


def check(pin: str, *, now: float | None = None) -> str:
    """Lockout-aware PIN check. Returns OK / BAD / LOCKOUT / LOCKED. Raises PinError when the
    keyring cannot be used or no PIN is configured — both refusals."""
    now = time.time() if now is None else now
    state = _failures()
    if state["locked_until"] > now:
        return LOCKED
    stored = _stored()
    if not stored:
        raise PinError("no desk PIN configured — run `cherrypick-desk pin-set`")
    if _matches(pin, stored):
        if state["failures"] or state["locked_until"]:
            _save_failures({"failures": [], "locked_until": 0.0})
        return OK
    recent = [t for t in state["failures"] if now - t < FAILURE_WINDOW_SECONDS] + [now]
    if len(recent) >= MAX_FAILURES:
        _save_failures({"failures": [], "locked_until": now + LOCKOUT_SECONDS})
        return LOCKOUT
    _save_failures({"failures": recent, "locked_until": 0.0})
    return BAD


def require(pin: str) -> None:
    """`check`, raising PinRejected on anything but OK."""
    outcome = check(pin)
    if outcome == OK:
        return
    if outcome == LOCKED:
        raise PinRejected(LOCKED, "PIN locked out after repeated failures — try again later")
    if outcome == LOCKOUT:
        raise PinRejected(
            LOCKOUT, f"PIN rejected — {MAX_FAILURES} failures within an hour; locked for an hour"
        )
    raise PinRejected(BAD, "PIN rejected")


# --------------------------------------------------------------------------- set / clear
def set_pin(new_pin: str, *, current: str | None = None) -> None:
    """Store (the verifier for) a new PIN. When a PIN is already set, `current` must pass
    `require` first — lockout included."""
    new_pin = str(new_pin or "")
    if len(new_pin.strip()) < MIN_LENGTH:
        raise PinError(f"PIN must be at least {MIN_LENGTH} characters")
    if _stored():
        require(current or "")
    try:
        keystore.put(_PIN_KEY, _format(new_pin))
    except keystore.KeystoreError as exc:
        raise PinError(str(exc)) from exc


def clear_pin(*, current: str | None = None) -> None:
    """Remove the PIN. Requires the current one when a PIN is set."""
    if not _stored():
        return
    require(current or "")
    try:
        keystore.delete(_PIN_KEY)
    except keystore.KeystoreError as exc:
        raise PinError(str(exc)) from exc
