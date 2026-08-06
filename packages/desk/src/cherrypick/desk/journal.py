"""Append-only audit journal — every desk decision, allowed or refused.

Refusals are recorded as deliberately as submissions. A log that only holds successes cannot answer
the question you actually ask it after something goes wrong ("what was attempted, and what stopped
it?"), and a run of refusals is itself the signal that something is probing or misconfigured.

Append-only JSONL, one line per event, opened in `"a"` mode so concurrent writers interleave whole
lines rather than corrupting each other. Nothing sensitive is ever written: account numbers are
masked to `****1234`, and the PIN and the raw confirmation code never enter a record at all — the
order *fingerprint* stands in for the code, since it identifies the order without being reusable.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import config as cfgmod
from .policy import mask_account


def _now() -> str:
    return datetime.now(UTC).isoformat()


def record(event: str, **fields: Any) -> dict:
    """Append one event. Never raises — an audit write must not be able to break a trade decision
    that has already been made correctly (the reverse — failing open on the *gates* — is what must
    never happen, and that is `policy.evaluate`'s job, not this module's)."""
    entry = {"ts": _now(), "event": event, **fields}
    if "account_number" in entry:
        entry["account"] = mask_account(entry.pop("account_number"))
    try:
        path = cfgmod.journal_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass
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
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        return []
    return out


def today_totals(day: str, path: Path | None = None) -> tuple[int, float]:
    """(orders submitted, risk committed) for `day` (an ISO date, UTC) — what the optional daily
    caps are measured against. Only `submitted` events count: a refused or proposed order consumed
    no risk budget, and counting proposals would let a rejected attempt eat the day's allowance."""
    orders = 0
    risk = 0.0
    for entry in read_all(path):
        if entry.get("event") != "submitted":
            continue
        if not str(entry.get("ts", "")).startswith(day):
            continue
        orders += 1
        try:
            value = entry.get("max_loss")
            risk += float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            continue
    return orders, risk
