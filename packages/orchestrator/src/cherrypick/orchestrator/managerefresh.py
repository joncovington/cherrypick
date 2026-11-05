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


def stored_snapshot(path: Path) -> tuple[str | None, datetime | None]:
    """(session, taken at) of the stored snapshot; (None, None) when there is none or it is unreadable."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    return (str(doc["session"]) if doc.get("session") else None), _when(doc.get("generated_at"))


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


def why_full(
    now: datetime, today: str, snap_session: str | None, snap_at: datetime | None, fill_at: datetime | None
) -> str | None:
    """Why this minute takes a full snapshot, or None for an ordinary re-mark."""
    if snap_session != today or snap_at is None:
        return "no snapshot today yet"
    if fill_at is not None and fill_at > snap_at:
        if (now - fill_at).total_seconds() >= FILL_SETTLE_SECONDS:
            return f"a fill at {fill_at.astimezone(now.tzinfo).strftime('%H:%M:%S')} since the snapshot"
        return None  # let the rest of the order land; the next minute takes it
    return None
