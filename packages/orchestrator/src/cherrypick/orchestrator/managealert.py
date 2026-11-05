"""Needs-a-look alerts: a name posted the first time it appears in a session's `attention` list.

manage's evaluation lists every short option near its strike, in the money or likely to be exercised
early (packages/manage `attention.py`). The full `manage` runs and the minute `manage-marks` re-marks
both rewrite that evaluation; after each, this posts the names not yet posted this session, one post
for all of them, and records them in `state/manage.attention-alerts.json`. A name that leaves the list
and comes back the same day is not posted again; the next session starts empty.

Pure apart from `run`, which reads the evaluation file, the state file and the notifier. It reads files
only and places nothing; a failed post is the notifier's to log, and the names are still recorded, so
a dead webhook never turns into a post every minute.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cherrypick.core.jsonio import write_json_atomic


def _items(doc: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for account in doc.get("accounts") or []:
        out += list(((account.get("attention") or {}).get("items")) or [])
    return out


def due(
    doc: dict[str, Any], sent: dict[str, Any] | None, session: str | None
) -> tuple[list[dict[str, Any]], dict]:
    """(the items on names not yet posted this session, the state to record)."""
    already = set((sent or {}).get("names") or []) if (sent or {}).get("session") == session else set()
    items = _items(doc)
    fresh = [i for i in items if i.get("underlying") not in already]
    names = sorted(already | {str(i.get("underlying")) for i in fresh})
    return fresh, {"session": session, "names": names}


def message(items: list[dict[str, Any]]) -> tuple[str, str]:
    """A title naming the names, and one line per leg: name, leg, level and why."""
    names = sorted({str(i.get("underlying")) for i in items})
    title = f"Needs a look: {', '.join(names)}"
    lines = [f"{i.get('underlying')} {i.get('leg')} -- {i.get('level')}: {i.get('why')}" for i in items]
    return title, "\n".join(lines) + "\nSee the console's Live → wheel."


def run(written: str | None, session: str | None, state_path: Path, notifier: Any) -> list[str]:
    """Post this evaluation's new names; returns them (empty when there is nothing new)."""
    if not written:
        return []
    try:
        doc = json.loads(Path(written).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    try:
        sent = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        sent = None
    fresh, state = due(doc, sent, session or doc.get("session"))
    if not fresh:
        return []
    write_json_atomic(state_path, state, indent=None)
    title, text = message(fresh)
    notifier.notify("WARNING", "manage.attention", title, text, session=state["session"])
    return sorted({str(i.get("underlying")) for i in fresh})


# --------------------------------------------------------------------------- the day's loss
def day_loss_due(
    doc: dict[str, Any], sent: dict[str, Any] | None, session: str | None
) -> dict[str, Any] | None:
    """The first account whose day's loss crossed its alert (manage's `limits.day_loss`), unless one
    was already posted this session; None otherwise."""
    if (sent or {}).get("session") == session:
        return None
    for account in doc.get("accounts") or []:
        day = ((account.get("limits") or {}).get("day_loss")) or {}
        if day.get("crossed") is True:
            return {"account": account.get("account"), **day}
    return None


def run_day_loss(written: str | None, session: str | None, state_path: Path, notifier: Any) -> bool:
    """Post the day's loss once a session when it crosses its alert (your `limits.day_loss_alert`, or
    `day_loss_alert_pct` of net liq). True when it posted."""
    if not written:
        return False
    try:
        doc = json.loads(Path(written).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    try:
        sent = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        sent = None
    session = session or doc.get("session")
    hit = day_loss_due(doc, sent, session)
    if hit is None:
        return False
    write_json_atomic(state_path, {"session": session, "pnl": hit.get("pnl")}, indent=None)
    notifier.notify(
        "WARNING",
        "manage.day_loss",
        f"Day's loss past your alert: ${hit['pnl']:,.2f}",
        f"{hit.get('account') or 'the account'}: the day's P&L on the marks is ${hit['pnl']:,.2f}, past the "
        f"alert at ${hit['alert']:,.2f}. Posted once a session; see the console's Live -> portfolio.",
        session=session,
    )
    return True
