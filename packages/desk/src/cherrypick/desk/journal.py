"""Append-only audit journal — every desk decision, allowed or refused.

Refusals are recorded as deliberately as submissions. A log that only holds successes cannot answer
the question you actually ask it after something goes wrong ("what was attempted, and what stopped
it?"), and a run of refusals is itself the signal that something is probing or misconfigured.

Append-only JSONL, one line per event, opened in `"a"` mode so concurrent writers interleave whole
lines rather than corrupting each other. Nothing sensitive is ever written: account numbers are
masked to `****1234`, and the PIN and the raw confirmation code never enter a record at all — the
order *fingerprint* stands in for the code, since it identifies the order without being reusable.

Two kinds of write. `record` never raises: an audit line for a refusal or a status read must not be
able to break a decision already made correctly. `record_strict` raises, and the submit path uses it
for the `submitting` line it writes BEFORE an order goes to the broker — an order the journal could
not account for would also be invisible to the daily caps, so no line means no submit.

Every line carries `ts` (UTC, unchanged for existing readers such as the orchestrator's desk
notifier) and `session_date`, the America/New_York date the daily caps are counted against.

Event names: `proposed`, `refused`, `submitting`, `submitted` (the broker accepted it — the event
the notifier cards), `failed`, `uncertain` (the submit raised and today's orders could not be read
back), `cancelled`, `cancel_failed`, `pin_set`, `pin_cleared`, `pin_lockout`.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cherrypick.core import clock as _clock

from . import config as cfgmod
from .policy import mask_account

# Every event that means "an order went (or may have gone) to the broker". Each counts once per
# ticket toward the daily caps, so a failed or uncertain attempt still spends the day's budget.
ATTEMPT_EVENTS = frozenset({"submitting", "submitted", "failed", "uncertain"})


class JournalError(RuntimeError):
    """A strict journal write that did not land."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _entry(event: str, fields: dict[str, Any]) -> dict:
    entry = {"ts": _now(), "session_date": _clock.today_iso(), "event": event, **fields}
    if "account_number" in entry:
        entry["account"] = mask_account(entry.pop("account_number"))
    return entry


def _append(entry: dict, *, sync: bool) -> None:
    path = cfgmod.journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, default=str) + "\n")
        if sync:
            fh.flush()
            os.fsync(fh.fileno())


def record(event: str, **fields: Any) -> dict:
    """Append one event. Never raises — see the module docstring for why that is right here and
    wrong for `record_strict`."""
    entry = _entry(event, fields)
    try:
        _append(entry, sync=False)
    except OSError:
        pass
    return entry


def record_strict(event: str, **fields: Any) -> dict:
    """Append one event and fsync it, or raise JournalError."""
    entry = _entry(event, fields)
    try:
        _append(entry, sync=True)
    except (OSError, ValueError, TypeError) as exc:
        raise JournalError(f"journal write failed: {type(exc).__name__}: {exc}") from exc
    return entry


def read_all(path: Path | None = None) -> list[dict]:
    """Every journal entry, oldest first. Unparseable lines are skipped, not fatal."""
    p = path or cfgmod.journal_path()
    if not p.exists():
        return []
    out = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                out.append(entry)
    except OSError:
        return []
    return out


def session_date_of(entry: dict) -> str | None:
    """The ET session date of an entry: its own `session_date`, or its UTC `ts` converted (lines
    written before `session_date` existed)."""
    if entry.get("session_date"):
        return str(entry["session_date"])
    try:
        ts = datetime.fromisoformat(str(entry.get("ts")))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(_clock.ET).date().isoformat()


def today_totals(day: str, path: Path | None = None) -> tuple[int, float]:
    """(submit attempts, worst case committed) for ET session date `day` — what the daily caps are
    measured against.

    An attempt is any ticket with a `submitting`, `submitted`, `failed` or `uncertain` line that
    day, counted once per ticket. Failures count on purpose: a submit that raised may still have
    reached the broker, and a cap that only counted confirmed fills would let a flapping connection
    place the day's budget twice. Refused and merely proposed orders cost nothing.
    """
    tickets: dict[str, float] = {}
    for n, entry in enumerate(read_all(path)):
        if entry.get("event") not in ATTEMPT_EVENTS or session_date_of(entry) != day:
            continue
        key = str(entry.get("ticket_id") or entry.get("order_id") or f"line-{n}")
        value = entry.get("max_loss")
        try:
            loss = float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            loss = 0.0
        tickets[key] = max(tickets.get(key, 0.0), loss)
    return len(tickets), sum(tickets.values())
