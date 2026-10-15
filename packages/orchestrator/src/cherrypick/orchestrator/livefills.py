"""`run.py live-fill-alerts` -- a LIVE fill alert within seconds, from the order-alert inboxes.

The trade notifier announces a live fill once the ledger records it: up to a minute for the tick to
confirm it, plus its own two-minute pass. The order-alert daemons (`cherrypick.core.live.alerts`, one
per armed module) see the broker's fill the moment it happens and write it to an inbox beside the
module's live ledger. This job reads those inboxes every few seconds while something is armed and
pushes one alert per filled order. Pushing stays here -- the suite's rule that a trading module never
talks to a notification channel.

It is an early word, not the record: the ledger still records the fill only once the module's own
tick confirms it with the broker, and the trade notifier's usual alert follows. Two daemons on one
account both see every order, so alerts are deduplicated by order id. Each order is attributed to the
module whose published order ids (`core.execution.publish_known_orders`) contain it; one that no live
ledger knows is announced as exactly that -- a manual order, or a placement no ledger recorded.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from cherrypick.core import home as _home
from cherrypick.core.live import alerts as _alerts

from cherrypick.notify import Notifier

from . import config as cfgmod


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


def inboxes(cfg: dict[str, Any]) -> dict[str, str]:
    """module -> its order-alert inbox path, for every enabled module with a live ledger."""
    out = {}
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        if not mcfg.get("live"):
            continue
        db = cfgmod.live_db_path(mcfg, name)
        if db is not None:
            out[name] = str(db.parent / _alerts.INBOX_FILENAME)
    return out


def run(
    cfg: dict[str, Any] | None = None, *, notifier: Any = None, today: str | None = None
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
    if state.get("day") != today:  # a new day: nothing carried, so yesterday's ids never block today's
        state = {"day": today, "after": {}, "notified": []}
    notified = set(state.get("notified") or [])
    owners = _owners()
    sent = []
    for module, path in sorted(inboxes(cfg).items()):
        after = (state.get("after") or {}).get(module)
        for row in _alerts.fills_after(path, after):
            state.setdefault("after", {})[module] = row["received_at"]
            oid = str(row["order_id"])
            if not row["received_at"].startswith(today) or oid in notified:
                continue
            notified.add(oid)
            owner = owners.get(oid)
            price = row.get("price")
            if owner:
                title = f"LIVE fill: {owner}"
                message = (
                    f"Order {oid} FILLED at {price} (broker alert; the ledger records it at the next tick)."
                )
            else:
                title = "LIVE fill: an order no live ledger knows"
                message = (
                    f"Order {oid} FILLED at {price}. No live module's ledger records this order -- a manual "
                    "order, or a placement nothing recorded. Check the broker."
                )
            notifier.notify("WARNING", f"live.fill.{oid}", title, message)
            sent.append({"order_id": oid, "module": owner, "price": price})
    state["notified"] = sorted(notified)
    _save_state(state)
    return {"ok": True, "sent": sent}
