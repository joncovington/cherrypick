"""`run.py live-fill-alerts` -- LIVE order notices within seconds, from the account's order events.

The trade notifier announces a live fill once the ledger records it: up to a minute for the tick to
confirm it, plus its own two-minute pass. The account-alert service (`cherrypick.core.live.
account_alerts`, kept up through every trading session) records every order status on the designated
account the moment the stream or its REST read sees it. This job reads that inbox every few seconds
and pushes one notice per order and kind (2026-11-02; fills only, and only while armed, before):

- **placed** -- the order reached the broker (received, routed, live);
- **filled**;
- **ended unfilled** -- cancelled, rejected, expired or removed.

An order seen placed and filled in the same pass is announced once, as filled. Pushing stays here --
the suite's rule that a trading module never talks to a notification channel.

It is early word, not the record: the ledger still records a fill only once the owning loop's tick
confirms it with the broker. Each order is attributed to the module whose published order ids
(`core.execution.publish_known_orders`) contain it. One no ledger knows yet but carrying an external
identifier is the suite's own (every seam submission is stamped), most likely placed this minute; one
with no identifier is announced as manual.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from cherrypick.core import execution as _execution
from cherrypick.core import home as _home
from cherrypick.core.live import account_alerts as _account

from cherrypick.notify import Notifier

from . import config as cfgmod

PLACED = frozenset({"received", "routed", "in flight", "live", "contingent"})


def state_path():
    return _home.state_dir() / "live_fill_alerts.json"


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(path)


def _owners() -> dict[str, str]:
    """order id -> the live module whose ledger published it."""
    out: dict[str, str] = {}
    directory = _home.state_dir() / "live_known_orders"
    try:
        files = list(directory.glob("*.json"))
    except OSError:
        return out
    for f in files:
        try:
            for oid in json.loads(f.read_text(encoding="utf-8")).get("order_ids") or []:
                out[str(oid)] = f.stem
        except (OSError, ValueError, AttributeError):
            continue
    return out


def kind_of(status: str) -> str | None:
    """'placed', 'filled', 'ended' or None (a status worth no notice: cancel requested, ...)."""
    if status == "filled":
        return "filled"
    if status in _execution.TERMINAL_UNFILLED:
        return "ended"
    if status in PLACED:
        return "placed"
    return None


def _who(event: dict, owners: dict[str, str]) -> tuple[str, str]:
    """(label for the title, sentence for the message)."""
    owner = owners.get(str(event["order_id"]))
    if owner:
        return owner, ""
    if event.get("external_identifier"):
        return "a live loop", " No ledger has published it yet; a loop's own order, placed this minute."
    return "manual", " No live ledger records it and it carries no suite identifier: a manual order."


def _notice(kind: str, event: dict, who: tuple[str, str]) -> tuple[str, str, str]:
    """(level, title, message) for one notice."""
    label, note = who
    oid, status = event["order_id"], event["status"]
    on = f" on {event['underlying_symbol']}" if event.get("underlying_symbol") else ""
    price = event.get("price")
    if kind == "filled":
        return (
            "WARNING",
            f"LIVE fill: {label}",
            f"Order {oid}{on} FILLED at {price} (broker report; the ledger records it next tick).{note}",
        )
    if kind == "ended":
        return "INFO", f"LIVE order {status}: {label}", f"Order {oid}{on} ended {status}, unfilled.{note}"
    return (
        "INFO",
        f"LIVE order placed: {label}",
        f"Order {oid}{on} is at the broker ({status}), limit {price}.{note}",
    )


def run(
    cfg: dict[str, Any] | None = None,
    *,
    notifier: Any = None,
    today: str | None = None,
    inbox: str | None = None,
) -> dict[str, Any]:
    cfg = cfgmod.load_config() if cfg is None else cfg
    today = today or date.today().isoformat()
    if notifier is None:
        notify_cfg = cfg.get("notify") or {}
        # The trade notifier's channels (`notify.trade_channels`) plus the desktop toast, as its own
        # LIVE alerts get -- real money warrants it.
        channels = sorted(set(notify_cfg.get("trade_channels", ["log"])) | {"desktop"})
        notifier = Notifier({**notify_cfg, "channels": channels})
    state = _load_state()
    if state.get("day") != today or not isinstance(state.get("after"), (int, type(None))):
        state = {"day": today, "after": None, "notified": []}  # a new day (or the pre-2026-11-02 shape)
    notified = set(state.get("notified") or [])
    read = _account.events_after(inbox or _account.inbox_path(), state.get("after"))
    if read:
        state["after"] = read[-1]["seq"]
    events = [e for e in read if str(e.get("received_at") or "").startswith(today)]
    filled_now = {str(e["order_id"]) for e in events if e["status"] == "filled"}
    owners = _owners()
    sent = []
    for event in events:
        kind = kind_of(event["status"])
        oid = str(event["order_id"])
        if kind is None or f"{oid}:{kind}" in notified:
            continue
        if kind == "placed" and oid in filled_now:
            continue  # placed and filled in one pass: the fill says both
        notified.add(f"{oid}:{kind}")
        who = _who(event, owners)
        level, title, message = _notice(kind, event, who)
        notifier.notify(level, f"live.order.{oid}.{kind}", title, message)
        sent.append({"order_id": oid, "kind": kind, "module": who[0], "status": event["status"]})
    state["notified"] = sorted(notified)
    _save_state(state)
    return {"ok": True, "sent": sent}
