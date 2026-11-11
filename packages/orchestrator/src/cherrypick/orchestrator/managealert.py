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
    """Every account's items, each with `_key`: the name on the main login (as the state file always
    held it), `<login> ****1234|NAME` on another, so the same name in two accounts posts for each."""
    from .managepost import account_ref

    out: list[dict[str, Any]] = []
    for account in doc.get("accounts") or []:
        main = (account.get("login") or "main") == "main"
        ref = account_ref(account)
        for i in ((account.get("attention") or {}).get("items")) or []:
            key = str(i.get("underlying")) if main else f"{ref}|{i.get('underlying')}"
            out.append({**i, "_key": key, "_account": None if main else ref})
    return out


def due(
    doc: dict[str, Any], sent: dict[str, Any] | None, session: str | None
) -> tuple[list[dict[str, Any]], dict]:
    """(the items on names not yet posted this session, the state to record)."""
    already = set((sent or {}).get("names") or []) if (sent or {}).get("session") == session else set()
    items = _items(doc)
    fresh = [i for i in items if i["_key"] not in already]
    names = sorted(already | {i["_key"] for i in fresh})
    return fresh, {"session": session, "names": names}


def _name(i: dict[str, Any]) -> str:
    return f"{i.get('underlying')} ({i['_account']})" if i.get("_account") else str(i.get("underlying"))


def message(items: list[dict[str, Any]]) -> tuple[str, str]:
    """A title naming the names, and one line per leg: name, leg, level and why. A name on another
    login carries its account."""
    names = sorted({_name(i) for i in items})
    title = f"Needs a look: {', '.join(names)}"
    lines = [f"{_name(i)} {i.get('leg')} -- {i.get('level')}: {i.get('why')}" for i in items]
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
    """The first account whose day's loss crossed its alert (manage's `limits.day_loss`) and has not
    been posted this session; None otherwise. Once a session PER ACCOUNT: `sent["accounts"]` lists the
    ones posted. A state from before named logins (no list) means the main login's was."""
    from .managepost import account_ref

    posted: set[str] = set()
    if (sent or {}).get("session") == session:
        listed = (sent or {}).get("accounts")
        if not isinstance(listed, list):
            return None  # the old shape: one post a session, already made
        posted = {str(a) for a in listed}
    for account in doc.get("accounts") or []:
        day = ((account.get("limits") or {}).get("day_loss")) or {}
        ref = account_ref(account)
        if day.get("crossed") is True and ref not in posted:
            return {"account": ref, "posted": sorted(posted | {ref}), **day}
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
    write_json_atomic(
        state_path,
        {"session": session, "pnl": hit.get("pnl"), "accounts": hit.get("posted") or []},
        indent=None,
    )
    notifier.notify(
        "WARNING",
        "manage.day_loss",
        f"Day's loss past your alert: ${hit['pnl']:,.2f}",
        f"{hit.get('account') or 'the account'}: the day's P&L on the marks is ${hit['pnl']:,.2f}, past the "
        f"alert at ${hit['alert']:,.2f}. Posted once a session; see the console's Live -> portfolio.",
        session=session,
    )
    return True
