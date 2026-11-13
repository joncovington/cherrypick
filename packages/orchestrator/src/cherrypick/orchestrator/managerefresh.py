"""When the minute `manage-marks` job takes a full broker snapshot instead of a re-mark (2026-11-04).

A re-mark moves the stored snapshot to the stream's quotes; it cannot see a trade made since. So the
job takes a full snapshot (`run.py manage`'s own path) when:

- **there is no snapshot for today yet** -- the first minute of its window (09:36 ET by default), so
  prices, P&L and needs-a-look start at the open rather than at the 10:00 run; or
- **a fill was heard after the stored snapshot was taken** (the `account-alerts` service's inbox,
  `account_alerts.db`, status `filled`), once that fill is `FILL_SETTLE_SECONDS` old: a multi-leg
  order fills leg by leg, so the wait takes them in one snapshot rather than one each.

Pure apart from the two readers, both read-only file reads. Nothing here touches the broker; the full
snapshot it asks for is `managesnap`'s read-only reads, the same as the scheduled runs.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

FILL_SETTLE_SECONDS = 30


def _when(stamp: object) -> datetime | None:
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", None):
        try:
            t = datetime.strptime(str(stamp), fmt) if fmt else datetime.fromisoformat(str(stamp))
        except (TypeError, ValueError):
            continue
        return t if t.tzinfo is not None else None
    return None


def _read(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def stored_snapshot(path: Path) -> tuple[str | None, datetime | None]:
    """(session, taken at) of the stored snapshot; (None, None) when there is none or it is unreadable.
    "Taken at" is when its reads began (`started_at`; older files only have `generated_at`, the end), so
    a fill that landed while the snapshot was being read counts as after it."""
    doc = _read(path)
    taken = _when(doc.get("started_at")) or _when(doc.get("generated_at"))
    return (str(doc["session"]) if doc.get("session") else None), taken


def held_legs(positions: object) -> dict[tuple[str, str], float] | None:
    """(account, leg symbol) -> signed quantity for everything a `positions` result holds; None when the
    result is missing, failed or has no accounts to read, so "cannot tell" is never a difference."""
    if not isinstance(positions, dict) or positions.get("ok") is False or "accounts" not in positions:
        return None
    out: dict[tuple[str, str], float] = {}
    for account in positions.get("accounts") or []:
        for underlying in account.get("underlyings") or []:
            for leg in underlying.get("legs") or []:
                symbol = str(leg.get("symbol") or "")
                if not symbol:
                    continue
                try:
                    qty = float(leg.get("quantity") or 0)
                except (TypeError, ValueError):
                    continue
                # The login joins the account on another login: two logins can share a last four,
                # and a leg moving between them is a change in both. Main's key is as it always was.
                login = account.get("login") or "main"
                who = str(account.get("account") or "")
                key = (who if login == "main" else f"{login} {who}", symbol)
                out[key] = out.get(key, 0.0) + qty
    return out


def stored_legs(path: Path, today: str) -> dict[tuple[str, str], float] | None:
    """The held legs of today's stored snapshot, or None when there is none for today."""
    doc = _read(path)
    if doc.get("session") != today:
        return None
    return held_legs(doc.get("positions"))


def leg_changes(stored: dict | None, current: dict | None) -> str | None:
    """What differs between two `held_legs` maps, or None when they match or either is unknown."""
    if stored is None or current is None:
        return None
    changed = sorted(
        {k for k in stored.keys() | current.keys() if abs(stored.get(k, 0.0) - current.get(k, 0.0)) > 1e-9}
    )
    if not changed:
        return None
    names = ", ".join(sym for _, sym in changed[:3])
    return f"positions differ from the snapshot ({names}{', ...' if len(changed) > 3 else ''})"


def latest_fill(inbox: Path) -> datetime | None:
    """When the newest fill was heard; None with no inbox or no fill."""
    if not inbox.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{inbox.as_posix()}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT MAX(received_at) FROM order_events WHERE status = 'filled'").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return _when(row[0]) if row and row[0] else None


def latest_fill_any(inboxes: list[Path]) -> datetime | None:
    """The newest fill across several inboxes (one per broker login): a snapshot reads every
    account, so a fill on any login is a reason to take one."""
    found = [at for at in (latest_fill(p) for p in inboxes) if at is not None]
    return max(found) if found else None


def why_full(
    now: datetime,
    today: str,
    snap_session: str | None,
    snap_at: datetime | None,
    fill_at: datetime | None,
    *,
    settle_seconds: int = FILL_SETTLE_SECONDS,
    min_gap_seconds: int = 0,
    positions_change: str | None = None,
) -> str | None:
    """Why this minute takes a full snapshot, or None for an ordinary re-mark.

    A fill since the snapshot waits until it is `settle_seconds` old (the rest of the order may be
    landing), and `positions_change` (what `leg_changes` found: an assignment, exercise or transfer,
    which arrive as no fill) is the second reason. Neither starts within `min_gap_seconds` of the stored
    snapshot's start; the request is kept, since the fill stays newer than the snapshot, and a later
    minute takes it. A day with no snapshot yet is never made to wait."""
    if snap_session != today or snap_at is None:
        return "no snapshot today yet"
    if (now - snap_at).total_seconds() < min_gap_seconds:
        return None
    if fill_at is not None and fill_at > snap_at:
        if (now - fill_at).total_seconds() >= settle_seconds:
            return f"a fill at {fill_at.astimezone(now.tzinfo).strftime('%H:%M:%S')} since the snapshot"
        return None  # let the rest of the order land; the next minute takes it
    return positions_change
