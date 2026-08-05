"""Notify tastylive Follow Feed fills (e.g. to Discord) as the traders post them.

Polls the Follow Feed's public read endpoints and pushes one concise line per newly seen order.
Same event contract as trade_notifier: a per-id watermark (one-shot, never re-notified), atomic
state, a single-writer lock, and seed-don't-backfill on first activation so switching it on doesn't
blast the last 50 orders in one burst.

Endpoints (undocumented, discovered in the tastytrade web platform's own bundle; no auth required
for the two GETs used here):
  GET https://follow.tastylive.com/api/traders        -> the roster; `id` is the feed's `trader_id`
  GET https://follow.tastylive.com/api/public_orders  -> the feed, 50 most recent, filterable

**This is the one notifier that makes a network call, so it never rides the watchdog tick** — it is
its own scheduled task (`follow_feed.task_name`). The watchdog's no-network invariant is the whole
reason trade_notifier reads files only; polling a third-party HTTP service from the reliability path
would hand the health check a new failure mode. A dead or slow feed degrades to "no notifications",
never to a failed tick.

The feed is undocumented and unversioned: every request is wrapped, every field is read defensively,
and a shape change or an outage returns a skip rather than raising.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from cherrypick.notify import Notifier

from . import config as cfgmod
from . import util

BASE_URL = "https://follow.tastylive.com"
_TRADERS_PATH = "/api/traders"
_ORDERS_PATH = "/api/public_orders"

_STATE = cfgmod.STATE_DIR / "follow_notify.json"
_LOCK = cfgmod.STATE_DIR / "follow_notify.lock"
_LOCK_STALE_SECONDS = 600  # a crashed holder must not wedge follow notification forever
_ID_CAP = 4000  # bound the remembered-id list
_HTTP_TIMEOUT = 8
# The feed returns the 50 most recent orders. After downtime every one of them is "new", and a
# 50-message burst is noise, not news -- push the newest few and watermark the rest silently.
_DEFAULT_MAX_PER_RUN = 8

# Sent explicitly: the default "Python-urllib" User-Agent is the sort of thing a CDN blocks, and an
# unattributed poller against someone else's service is bad manners.
_USER_AGENT = "cherrypick-follow-notifier/1.0"


def _acquire_lock() -> bool:
    cfgmod.ensure_dirs()
    try:
        if _LOCK.exists() and time.time() - _LOCK.stat().st_mtime > _LOCK_STALE_SECONDS:
            _LOCK.unlink()
    except OSError:
        pass
    try:
        fd = os.open(_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    return True


def _release_lock() -> None:
    try:
        _LOCK.unlink()
    except OSError:
        pass


def _save_state(state: dict) -> None:
    # Atomic replace: the task could overlap a manual `cherrypick notify-follow`, and a plain
    # truncate-then-write could leave a half-written state file.
    cfgmod.ensure_dirs()
    tmp = _STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, _STATE)


# --------------------------------------------------------------------------- feed access
def _get_json(path: str, params: list[tuple[str, str]] | None = None) -> Any:
    """GET one feed endpoint. Returns the decoded body, or None on any failure — a third-party
    outage, a timeout, or a non-JSON error page must degrade to "nothing to notify"."""
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            if not 200 <= resp.status < 300:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def _filter_params(filters: dict) -> list[tuple[str, str]]:
    """Translate the config `filters` block into the feed's own query parameters, so the narrowing
    happens server-side. Names/shapes are the platform's, not ours: repeated `traders[]` and
    `underlying_symbols[]`, and the `attrs[...]` family."""
    params: list[tuple[str, str]] = []
    for name in filters.get("traders", []) or []:
        params.append(("traders[]", str(name)))
    for sym in filters.get("underlying_symbols", []) or []:
        params.append(("underlying_symbols[]", str(sym).upper()))
    if filters.get("earnings_only"):
        params.append(("attrs[is_earnings_play]", "true"))
    open_close = filters.get("open_close")
    if open_close in ("O", "C"):
        params.append(("attrs[open_close]", open_close))
    if filters.get("strategy"):
        params.append(("strategy", str(filters["strategy"])))
    return params


def fetch_trader_names() -> dict[int, str]:
    """{trader_id: display name} from the roster. `/api/traders.id` IS the feed's `trader_id`.
    Empty on failure — the formatter falls back to the bare id rather than skipping the push."""
    body = _get_json(_TRADERS_PATH)
    if not isinstance(body, dict):
        return {}
    names = {}
    for t in body.get("traders") or []:
        try:
            names[int(t["id"])] = str(t.get("name") or f"trader {t['id']}")
        except (KeyError, TypeError, ValueError):
            continue
    return names


def fetch_orders(filters: dict) -> list[dict]:
    body = _get_json(_ORDERS_PATH, _filter_params(filters))
    if not isinstance(body, dict):
        return []
    orders = body.get("public_orders")
    return [o for o in orders if isinstance(o, dict) and o.get("id") is not None] if orders else []


# --------------------------------------------------------------------------- formatting
def _direction(order: dict) -> str:
    """The platform's own wording: a net-debit order reads "Bought", a net-credit one "Sold".

    Single-leg orders (a lone option, or stock) carry order_type "limit" rather than either, so fall
    back to the leg actions — "buytoclose"/"selltoopen" and friends. Without this the feed's own
    "Sold Stock" rendered as the meaningless "Limit Stock"."""
    order_type = str(order.get("order_type") or "")
    if order_type == "net_debit":
        return "Bought"
    if order_type == "net_credit":
        return "Sold"
    actions = {str(leg.get("action") or "").lower() for leg in order.get("order_legs") or []}
    actions.discard("")
    if actions and all(a.startswith("buy") for a in actions):
        return "Bought"
    if actions and all(a.startswith("sell") for a in actions):
        return "Sold"
    return order_type.replace("_", " ").title() or "Traded"


def _open_close(order: dict) -> str:
    """OPEN / CLOSE / "" — from the legs, which is where the platform carries it. A roll touches
    both, so anything mixed gets no tag rather than a misleading one."""
    marks = {str(leg.get("open_close") or "") for leg in order.get("order_legs") or []}
    marks.discard("")
    if marks == {"O"}:
        return "OPEN"
    if marks == {"C"}:
        return "CLOSE"
    return ""


def _underlyings(order: dict) -> str:
    seen = []
    for leg in order.get("order_legs") or []:
        sym = leg.get("underlying_symbol")
        if sym and sym not in seen:
            seen.append(str(sym))
    return "/".join(seen)


def _fmt_leg(leg: dict) -> str:
    """One leg as strike+right, e.g. '122P'. Falls back to the raw OCC symbol for anything that
    isn't a plain option leg (equity legs carry no strike)."""
    strike, right = leg.get("strike_price"), leg.get("call_or_put")
    if strike is None or not right:
        return str(leg.get("symbol") or leg.get("underlying_symbol") or "?")
    try:
        strike_str = f"{float(strike):g}"
    except (TypeError, ValueError):
        strike_str = str(strike)
    return f"{strike_str}{right}"


def _legs_summary(order: dict, max_legs: int = 6) -> str:
    legs = order.get("order_legs") or []
    if not legs:
        return ""
    # An equity leg's fallback token IS its underlying, which the line already carries — rendering
    # both gave "NVTS NVTS". Nothing to add for a stock leg beyond the symbol already shown.
    tokens = [t for t in (_fmt_leg(leg) for leg in legs[:max_legs]) if t not in _underlyings(order)]
    if not tokens:
        return ""
    shown = "/".join(tokens)
    if len(legs) > max_legs:
        shown += f"/+{len(legs) - max_legs}"
    expiries = sorted({str(leg.get("expiration_date")) for leg in legs if leg.get("expiration_date")})
    if expiries:
        shown += " " + (expiries[0] if len(expiries) == 1 else f"{expiries[0]}..{expiries[-1]}")
    return shown


def _price(order: dict) -> str:
    price = order.get("price_string") or order.get("price")
    if price in (None, ""):
        return ""
    # db/cr comes from the direction, not just order_type — a "limit" single-leg order has no net_*
    # type but is still unambiguously a debit or a credit, and the feed's own UI labels it that way.
    suffix = {"Bought": "db", "Sold": "cr"}.get(_direction(order), "")
    return f"${price} {suffix}".strip()


def _comment(order: dict, limit: int = 220) -> str:
    """The trader's own rationale, when they left one — the most useful part of the feed, and the
    reason this is worth a push rather than a dashboard panel."""
    for c in order.get("comments") or []:
        body = str(c.get("body") or "").strip()
        if body:
            return body if len(body) <= limit else body[: limit - 1] + "…"
    return ""


def format_order(order: dict, trader_names: dict[int, str]) -> str:
    """One feed order as a single push line."""
    try:
        trader_id = int(order.get("trader_id"))
    except (TypeError, ValueError):
        trader_id = -1
    trader = trader_names.get(trader_id, f"trader {order.get('trader_id', '?')}")
    strategy = str(order.get("strategy") or "order")
    head = f"\U0001f4e1 Follow — {trader} {_direction(order)} {strategy}"

    parts = [p for p in (_underlyings(order), _legs_summary(order), _price(order)) if p]
    tags = [t for t in (_open_close(order), "Earnings" if order.get("is_earnings_play") else "") if t]
    if order.get("is_hedge"):
        tags.append("Hedge")
    if order.get("is_scalp_trade"):
        tags.append("Scalp")
    if tags:
        parts.append("[" + " ".join(tags) + "]")

    line = f"{head} — {' '.join(parts)}" if parts else head
    comment = _comment(order)
    return f"{line}\n“{comment}”" if comment else line


# --------------------------------------------------------------------------- entrypoint
def _sort_key(order: dict) -> tuple:
    """Chronological, so a multi-order run reads in the order the trades happened. `executed_at` is
    the platform's own timestamp; `id` breaks ties and covers a null."""
    return (str(order.get("executed_at") or ""), int(order.get("id") or 0))


def run(cfg: dict | None = None) -> dict:
    cfg = cfg or cfgmod.load_config()
    settings = cfgmod.follow_feed_settings(cfg)
    if not settings["enabled"]:
        return {"ok": True, "skipped": "follow_feed not enabled"}

    if not _acquire_lock():
        return {"ok": True, "skipped": "another follow-notify run holds the lock"}
    try:
        notify_cfg = cfg.get("notify", {})
        notifier = Notifier({**notify_cfg, "channels": settings["channels"]})
        max_per_run = max(1, int(settings["max_per_run"] or _DEFAULT_MAX_PER_RUN))

        orders = fetch_orders(settings["filters"])
        if not orders:
            # An empty list is indistinguishable from a fetch failure here, and both mean the same
            # thing: nothing to push, state untouched, try again next tick.
            return {"ok": True, "orders_seen": 0, "notified": 0}

        state = util.read_json(_STATE)
        if not state.get("notified_ids"):  # first activation — seed, don't backfill
            state["notified_ids"] = [int(o["id"]) for o in orders]
            _save_state(state)
            return {"ok": True, "seeded": True, "orders_seen": len(orders)}

        seen = set(state["notified_ids"])
        fresh = sorted((o for o in orders if int(o["id"]) not in seen), key=_sort_key)
        if not fresh:
            return {"ok": True, "orders_seen": len(orders), "notified": 0}

        # After downtime the whole window is "new". Push the newest max_per_run and watermark the
        # older ones silently, so a gap costs you the tail rather than a 50-message flood.
        suppressed = fresh[:-max_per_run] if len(fresh) > max_per_run else []
        to_push = fresh[-max_per_run:]

        trader_names = fetch_trader_names() if to_push else {}
        for order in to_push:
            notifier.notify(
                "INFO",
                f"follow.order.{order['id']}",
                "Follow feed",
                format_order(order, trader_names),
            )
        for order in fresh:
            seen.add(int(order["id"]))
        state["notified_ids"] = sorted(seen)[-_ID_CAP:]
        _save_state(state)
        return {
            "ok": True,
            "orders_seen": len(orders),
            "notified": len(to_push),
            "suppressed": len(suppressed),
        }
    finally:
        _release_lock()
