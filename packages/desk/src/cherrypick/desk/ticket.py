"""Two-phase commit: a proposal becomes a ticket, and only a matching confirmation submits it.

The confirmation code is **derived from the order itself** — an HMAC of the canonical order, rendered
in a short unambiguous alphabet. That binding is the whole idea: the code is not a password checked
against a stored copy, it is a fingerprint of the order. Change the account, a strike, the price, a
size or any other field and the code changes, so a confirmation can only ever ratify the exact order
that produced it.

The HMAC key is a random secret kept in the keyring (created on first use), so a pending file cannot
be rewritten with a matching fingerprint and code by someone who can only write files. The code is
not stored in the ticket at all; it is shown once by `propose` and recomputed at confirm.

Further properties, each closing a specific hole:

* **Single use** — the pending file is claimed by an atomic rename before anything is checked, so two
  confirmations of one ticket cannot both reach the broker, and a wrong code, a wrong PIN or an
  expired ticket all leave it spent.
* **Short expiry** — an abandoned proposal cannot be revived later from scrollback. `expires_at` is
  sealed by the HMAC and must also sit within the configured TTL of `created_at`, so editing the
  file to stretch it fails twice.
* **Tamper-evident** — the fingerprint is recomputed from the stored order at confirm time.
* **No full account number on disk** — a ticket holds the masked account; the full one is resolved
  from the broker at confirm and must reproduce the fingerprint.

The honest limit, stated so nobody over-trusts this: the code is visible to whoever ran `propose`,
and anything running as this OS user can read the keyring. The code proves *this exact order was
reviewed*, not *a human reviewed it* — that is the PIN's job, and the `policy.py` gates bind
regardless of either.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from cherrypick.core.redact import mask_account

from . import config as cfgmod
from . import keystore

# Crockford-style alphabet: no 0/O/1/I/L/U — a code is read aloud and typed back, and those are the
# pairs that get transcribed wrong. 6 chars over 30 symbols is ~29 bits, ample for a short-lived,
# single-use token that authorizes nothing on its own (the PIN and the gates still run).
_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_LEN = 6
_SECRET_KEY = "ticket_hmac_secret"
_TICKET_ID = re.compile(r"^[0-9a-f]{8,32}$")
_CLOCK_SKEW_SECONDS = 5.0


class TicketError(RuntimeError):
    """A ticket that cannot be consumed — missing, expired, already used, tampered, or mismatched."""


# --------------------------------------------------------------------------- the key
def _secret() -> bytes:
    """The HMAC key, created on first use. Any keyring failure refuses."""
    try:
        value = keystore.get(_SECRET_KEY)
        if not value:
            keystore.put(_SECRET_KEY, secrets.token_hex(32))
            value = keystore.get(_SECRET_KEY)
    except keystore.KeystoreError as exc:
        raise TicketError(f"ticket key unavailable: {exc}") from exc
    try:
        key = bytes.fromhex(str(value or ""))
    except ValueError as exc:
        raise TicketError("ticket key in the keyring is malformed — refusing") from exc
    if len(key) < 16:
        raise TicketError("ticket key in the keyring is missing or too short — refusing")
    return key


def _mac(label: str, payload: str) -> bytes:
    return hmac.new(_secret(), f"{label}:{payload}".encode(), hashlib.sha256).digest()


# --------------------------------------------------------------------------- canonical form
def _num(value: Any) -> str:
    """Exact decimal text for a number, so 1.1 and 1.10001 can never fingerprint alike (the old
    four-decimal rounding let them)."""
    if isinstance(value, bool) or value is None:
        return json.dumps(value)
    try:
        return format(Decimal(str(value)).normalize(), "f")
    except (InvalidOperation, ValueError):
        return f"str:{value}"


def canonical(order: dict[str, Any], account_number: str | None) -> str:
    """The exact bytes the fingerprint is taken over.

    Total, so the same order cannot fingerprint two ways and no field can change without changing
    it: keys sorted, numbers as exact decimals, leg order made positional-independent by sorting,
    and every field of the order — including ones the parser refuses today, such as `stop_trigger`
    and `external_identifier` — rides along. The account is included so a code from one account can
    never ratify the same structure on another.
    """
    legs = []
    for leg in order.get("legs") or []:
        leg = leg if isinstance(leg, dict) else {"raw": leg}
        known = {
            "instrument_type": str(leg.get("instrument_type", "")).strip(),
            "symbol": str(leg.get("symbol", "")).strip().upper(),
            "action": str(leg.get("action", "")).strip().lower(),
            "quantity": _num(leg.get("quantity")),
        }
        extra = {k: json.dumps(v, sort_keys=True, default=str) for k, v in leg.items() if k not in known}
        legs.append({**known, "extra": extra})
    legs.sort(key=lambda leg: json.dumps(leg, sort_keys=True))

    payload = {
        "account": str(account_number or ""),
        "order_type": str(order.get("order_type") or "Limit"),
        "time_in_force": str(order.get("time_in_force") or "Day"),
        "price": _num(order.get("price")),
        "price_effect": str(order.get("price_effect") or "").strip().lower(),
        "stop_trigger": _num(order.get("stop_trigger")),
        "external_identifier": str(order.get("external_identifier") or ""),
        "legs": legs,
    }
    named = set(payload) | {"legs"}
    payload["extra"] = {
        k: json.dumps(v, sort_keys=True, default=str) for k, v in order.items() if k not in named
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def fingerprint(order: dict[str, Any], account_number: str | None) -> str:
    return _mac("desk-fp", canonical(order, account_number)).hex()


def code_for(order: dict[str, Any], account_number: str | None) -> str:
    """The human-readable confirmation code for this exact order."""
    n = int.from_bytes(_mac("desk-code", canonical(order, account_number))[:8], "big")
    out = []
    for _ in range(_CODE_LEN):
        n, rem = divmod(n, len(_ALPHABET))
        out.append(_ALPHABET[rem])
    return "".join(out)


def _seal(record: dict[str, Any]) -> str:
    fields = [
        record.get("ticket_id"),
        record.get("account"),
        record.get("fingerprint"),
        repr(record.get("created_at")),
        repr(record.get("expires_at")),
    ]
    return _mac("desk-seal", json.dumps(fields)).hex()


# --------------------------------------------------------------------------- files
def _pending_path(ticket_id: str) -> Path:
    if not _TICKET_ID.match(str(ticket_id or "")):
        raise TicketError(f"not a ticket id: {ticket_id!r}")
    return cfgmod.desk_dir() / f"pending-{ticket_id}.json"


def create(
    order: dict[str, Any], account_number: str | None, *, ttl_seconds: int, extra: dict | None = None
) -> dict:
    """Write a pending ticket and return it, plus the code to show the human (the code itself is
    never written to disk)."""
    directory = cfgmod.desk_dir()
    directory.mkdir(parents=True, exist_ok=True)
    ticket_id = secrets.token_hex(4)
    now = time.time()
    record = {
        **(extra or {}),
        "ticket_id": ticket_id,
        "account": mask_account(account_number),
        "fingerprint": fingerprint(order, account_number),
        "order": order,
        "created_at": now,
        "expires_at": now + max(1, int(ttl_seconds)),
    }
    record["seal"] = _seal(record)
    code = code_for(order, account_number)
    path = _pending_path(ticket_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    try:  # best-effort 0600; on Windows the ACL already limits to the user profile
        os.chmod(path, 0o600)
    except OSError:
        pass
    return {**record, "code": code}


def claim(ticket_id: str) -> dict:
    """Atomically claim a pending ticket and return its record. From here on the ticket is spent,
    whatever the later checks say — whichever process wins the rename owns it, and a wrong code or
    PIN cannot be retried against it."""
    path = _pending_path(ticket_id)
    if not path.exists():
        raise TicketError(
            f"no pending ticket {ticket_id!r} (already used, expired and cleaned, or never created)"
        )
    claimed = path.with_suffix(".json.claimed")
    try:
        os.replace(path, claimed)
    except OSError as exc:
        raise TicketError(f"ticket {ticket_id!r} could not be claimed: {exc}") from exc
    try:
        record = json.loads(claimed.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TicketError(f"ticket {ticket_id!r} is unreadable: {exc}") from exc
    if not isinstance(record, dict) or record.get("ticket_id") != ticket_id:
        raise TicketError(f"ticket {ticket_id!r} does not describe itself — refusing")
    return record


def check_fresh(record: dict, *, max_ttl_seconds: float, now: float | None = None) -> None:
    """Seal and timing: no network, so it runs before the PIN prompt."""
    now = time.time() if now is None else now
    if not hmac.compare_digest(_seal(record), str(record.get("seal", ""))):
        raise TicketError(f"ticket {record.get('ticket_id')!r} fails its seal — refusing (edited on disk?)")
    try:
        created = float(record["created_at"])
        expires = float(record["expires_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TicketError("ticket has no readable timestamps") from exc
    if created > now + _CLOCK_SKEW_SECONDS:
        raise TicketError("ticket was created in the future — refusing")
    if expires - created > float(max_ttl_seconds):
        raise TicketError(f"ticket lifetime exceeds the configured {int(max_ttl_seconds)}s — re-propose")
    if now > expires:
        raise TicketError(f"ticket {record.get('ticket_id')!r} expired — re-propose to get a fresh one")


def check_binding(record: dict, code: str, account_number: str | None) -> None:
    """The order, the account resolved now, and the typed code must all agree with the ticket."""
    if mask_account(account_number) != record.get("account"):
        raise TicketError("the resolved account is not the one this ticket was proposed on")
    order = record.get("order") or {}
    if not hmac.compare_digest(fingerprint(order, account_number), str(record.get("fingerprint", ""))):
        raise TicketError(
            f"ticket {record.get('ticket_id')!r} does not match its own order — refusing (tampered on disk?)"
        )
    expected = code_for(order, account_number)
    if not hmac.compare_digest(expected.upper(), str(code or "").strip().upper()):
        raise TicketError("confirmation code does not match this order")


def consume(ticket_id: str, code: str, account_number: str | None, *, max_ttl_seconds: float) -> dict:
    """claim + check_fresh + check_binding in one call. Raises TicketError; the ticket is spent
    either way."""
    record = claim(ticket_id)
    check_fresh(record, max_ttl_seconds=max_ttl_seconds)
    check_binding(record, code, account_number)
    return record


def release(ticket_id: str) -> None:
    """Drop a claimed ticket's files. Called after every confirm, success or failure — a claimed
    ticket is spent either way, so a failed submit needs a fresh proposal rather than a retry that
    could double-fire."""
    try:
        base = _pending_path(ticket_id)
    except TicketError:
        return
    for suffix in (".json.claimed", ".json", ".json.tmp"):
        try:
            base.with_suffix(suffix).unlink()
        except OSError:
            pass


def _expired_or_junk(path: Path, now: float) -> bool:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        return now > float(record.get("expires_at", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return True


def purge_expired() -> int:
    """Delete pending tickets past their expiry, plus claimed and half-written leftovers whose
    ticket has expired (a confirm that crashed mid-way leaves a `.claimed` file behind). Returns how
    many files were removed."""
    directory = cfgmod.desk_dir()
    if not directory.exists():
        return 0
    removed = 0
    now = time.time()
    for pattern in ("pending-*.json", "pending-*.json.claimed", "pending-*.json.tmp"):
        for path in directory.glob(pattern):
            if pattern.endswith(".tmp"):
                try:  # a .tmp younger than a minute may be a create() still writing it
                    if now - path.stat().st_mtime < 60:
                        continue
                except OSError:
                    continue
            if _expired_or_junk(path, now):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
    return removed
