"""Two-phase commit: a proposal becomes a ticket, and only a matching confirmation submits it.

The confirmation code is **derived from the order itself** — `sha256(canonical order)` rendered in a
short unambiguous alphabet. That binding is the whole idea: the code is not a password to be checked
against a stored copy, it is a fingerprint of the order. Change the account, a strike, the price, or
a quantity by one and the code changes, so a confirmation can only ever ratify the exact order that
produced it. Confirming is therefore an attestation about *contents*, not merely about intent.

Three further properties, each closing a specific hole:

* **Single use** — the pending file is consumed by an atomic rename before submission, so two
  confirmations of one ticket cannot both reach the broker (the loser finds nothing to consume).
* **Short expiry** — an abandoned proposal cannot be revived later from scrollback.
* **Tamper-evident** — the code is recomputed from the stored order at confirm time and compared to
  the stored code. Hand-editing the pending file to point at a different order invalidates it,
  because the recomputed fingerprint no longer matches.

The honest limit, stated so nobody over-trusts this: the code is visible to whoever ran `propose`.
It proves *this exact order was reviewed*, not *a human reviewed it* — that is the PIN's job (see
`pin.py`), and the gates in `policy.py` are what bind regardless of either.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from . import config as cfgmod

# Crockford-style alphabet: no 0/O/1/I/L/U — a code is read aloud and typed back, and those are the
# pairs that get transcribed wrong. 6 chars over 26 symbols is ~28 bits, ample for a 3-minute,
# single-use, non-guessable-benefit token (it authorizes nothing on its own; the gates still run).
_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_LEN = 6


class TicketError(RuntimeError):
    """A ticket that cannot be consumed — missing, expired, already used, tampered, or mismatched."""


def canonical(order: dict[str, Any], account_number: str | None) -> str:
    """The exact bytes the fingerprint is taken over.

    Canonicalization has to be total, or the same order could fingerprint two ways and a legitimate
    confirmation would fail: keys sorted, whitespace fixed, numbers normalized through float, leg
    order made positional-independent by sorting. The account is included so a code from one account
    can never ratify the same structure on another.
    """
    legs = sorted(
        (
            {
                "instrument_type": str(leg.get("instrument_type", "")).strip(),
                "symbol": str(leg.get("symbol", "")).strip().upper(),
                "action": str(leg.get("action", "")).strip().lower(),
                "quantity": int(leg.get("quantity", 0)),
            }
            for leg in order.get("legs") or []
        ),
        key=lambda leg: (leg["symbol"], leg["action"], leg["quantity"]),
    )
    payload = {
        "account": str(account_number or ""),
        "order_type": str(order.get("order_type") or "Limit"),
        "time_in_force": str(order.get("time_in_force") or "Day"),
        "price": f"{float(order.get('price', 0)):.4f}",
        "price_effect": str(order.get("price_effect") or "").strip().lower(),
        "legs": legs,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def fingerprint(order: dict[str, Any], account_number: str | None) -> str:
    return hashlib.sha256(canonical(order, account_number).encode("utf-8")).hexdigest()


def code_for(order: dict[str, Any], account_number: str | None) -> str:
    """The human-readable confirmation code for this exact order."""
    digest = hashlib.sha256(("desk-code:" + canonical(order, account_number)).encode("utf-8")).digest()
    n = int.from_bytes(digest[:8], "big")
    out = []
    for _ in range(_CODE_LEN):
        n, rem = divmod(n, len(_ALPHABET))
        out.append(_ALPHABET[rem])
    return "".join(out)


def _pending_path(ticket_id: str) -> Path:
    return cfgmod.desk_dir() / f"pending-{ticket_id}.json"


def create(
    order: dict[str, Any], account_number: str | None, *, ttl_seconds: int, extra: dict | None = None
) -> dict:
    """Write a pending ticket and return it (including the code to show the human)."""
    directory = cfgmod.desk_dir()
    directory.mkdir(parents=True, exist_ok=True)
    ticket_id = secrets.token_hex(4)
    now = time.time()
    record = {
        "ticket_id": ticket_id,
        "code": code_for(order, account_number),
        "fingerprint": fingerprint(order, account_number),
        "account_number": account_number,
        "order": order,
        "created_at": now,
        "expires_at": now + max(1, int(ttl_seconds)),
        **(extra or {}),
    }
    path = _pending_path(ticket_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    try:  # best-effort 0600; on Windows the ACL already limits to the user profile
        os.chmod(path, 0o600)
    except OSError:
        pass
    return record


def consume(ticket_id: str, code: str) -> dict:
    """Validate and atomically claim a ticket. Returns the record; raises TicketError otherwise.

    The claim (a rename out of the way) happens BEFORE any validation that could pass, so a
    concurrent second confirmation of the same ticket cannot also proceed — whichever process wins
    the rename owns the submission, and the loser sees "already used".
    """
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

    if time.time() > float(record.get("expires_at", 0)):
        raise TicketError(f"ticket {ticket_id!r} expired — re-propose to get a fresh one")

    # Tamper check: recompute from the stored order and compare both the fingerprint and the code.
    # Editing the file to swap in a different order changes both, so neither will match.
    recomputed = fingerprint(record.get("order") or {}, record.get("account_number"))
    if not secrets.compare_digest(recomputed, str(record.get("fingerprint", ""))):
        raise TicketError(f"ticket {ticket_id!r} does not match its own order — refusing (tampered on disk?)")

    expected = code_for(record.get("order") or {}, record.get("account_number"))
    if not secrets.compare_digest(expected.upper(), str(code or "").strip().upper()):
        raise TicketError("confirmation code does not match this order")

    return record


def release(ticket_id: str) -> None:
    """Drop a claimed ticket's file. Called after a submission attempt, success or failure — a
    claimed ticket is spent either way, so a failed submit needs a fresh proposal rather than a
    retry that could double-fire."""
    for suffix in (".json.claimed", ".json", ".json.tmp"):
        try:
            _pending_path(ticket_id).with_suffix(suffix).unlink()
        except OSError:
            pass


def purge_expired() -> int:
    """Delete pending tickets past their expiry. Returns how many were removed."""
    directory = cfgmod.desk_dir()
    if not directory.exists():
        return 0
    removed = 0
    now = time.time()
    for path in directory.glob("pending-*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if now > float(record.get("expires_at", 0)):
                path.unlink()
                removed += 1
        except (OSError, ValueError):
            try:
                path.unlink()  # unreadable pending files are junk; clearing them is not a data loss
                removed += 1
            except OSError:
                pass
    return removed
