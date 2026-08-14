"""Notify tastylive Follow Feed fills (e.g. to Discord) as the traders post them.

Polls the Follow Feed's public read endpoints and pushes one order per newly seen fill: a plain
three-line message for the log floor and any non-Discord channel, and a colored Discord embed card
for the channel this is actually read in (`format_order`/`build_embed` below). Same event contract as
trade_notifier: a per-id watermark (one-shot, never re-notified), atomic state, a single-writer lock,
and seed-don't-backfill on first activation so switching it on doesn't blast the last 50 orders in
one burst.

**The rendering half is shared with a sibling repo and must stay identical.** The repo
`joncovington/follow-feed-notifier` is a standalone extraction of this feed reader that runs on
GitHub Actions; it owns the same
`_direction`/`_leg_sign`/`_quantity`/`_expiry`/`_spot`/`format_order`/`build_embed` logic. The
formatting is where the bugs live — the feed is undocumented, so every awkward case (a single-leg
order whose `order_type` contradicts its leg, equity legs tagged "S" not "E", slash-prefixed futures
symbols, a futures price that is a level rather than a credit) was found by watching real orders, and
each has now been fixed twice. Treat the two as one implementation in two places: when you change a
formatter here, port it there in the same session, and vice versa. Verify by rendering the live feed
through both and diffing — they should be byte-identical, glyphs included (`×`, not `x`; `–`, not
`-`). Last verified identical across all 50 feed orders, text and embeds: 2026-08-13.

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
from datetime import datetime, timezone
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
# Ported from the standalone follow-feed-notifier (the same feed, refined independently into a card
# style: open/close leads the line rather than Bought/Sold, size and spot give strikes context, and
# the Discord push is a colored embed card rather than a plain text blob). Kept as one push per order
# — the standalone runs its own poll loop, this module still owns the watermark/lock/task wiring.

# Stripe colors for the Discord embed. Green/red is CASH direction, the same language as the
# Lossdog cards: the feed never tells us the P&L, so the stripe answers "which way did money move on
# this order", never "did the trade win" — buying back a winning credit spread still stripes red,
# because money went out. A futures outright quotes a level, not cash (nobody pays $29,728.75 to buy
# one MNQ), and an order whose direction the feed didn't say has no answer; both stay neutral.
COLOR_CREDIT = 0x22C55E  # green — money came in (a net credit)
COLOR_DEBIT = 0xEF4444  # red — money went out (a net debit)
COLOR_NEUTRAL = 0x6B7280  # slate — futures (a level, not cash), or a direction the feed didn't say

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _direction(order: dict) -> str:
    """The platform's own wording: a net-debit order reads "Bought", a net-credit one "Sold".

    The LEGS win when they all sit on one side, and order_type is the tiebreak for a genuine package
    (a vertical's legs point both ways, so only the net says whether it was a debit or a credit).
    Precedence matters: the feed ships single-leg orders whose order_type contradicts the leg — a
    lone "selltoclose" call tagged net_debit — and taking order_type first printed that credit as
    "$5.95 db". A one-sided order has no "net" to speak of; the action is the fact.

    Single-leg orders also often carry order_type "limit" rather than either net_*, which is the
    other reason the leg has to be readable on its own — it rendered as the meaningless "Limit Stock"."""
    actions = {str(leg.get("action") or "").lower() for leg in order.get("order_legs") or []}
    actions.discard("")
    if actions and all(a.startswith("buy") for a in actions):
        return "Bought"
    if actions and all(a.startswith("sell") for a in actions):
        return "Sold"
    order_type = str(order.get("order_type") or "")
    if order_type == "net_debit":
        return "Bought"
    if order_type == "net_credit":
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


def _stripe_color(order: dict) -> int:
    """Cash direction as the stripe. Futures are checked first: `_direction` still reads
    Bought/Sold on an outright, but no premium changed hands, so coloring it would invent a cash
    flow — the same reason `_price` drops the db/cr suffix on those rows. A roll gets the color of
    its NET (the order_type tiebreak inside `_direction`), which is the one thing green/red can say
    about it honestly."""
    if _is_futures(order):
        return COLOR_NEUTRAL
    match _direction(order):
        case "Sold":
            return COLOR_CREDIT
        case "Bought":
            return COLOR_DEBIT
        case _:
            return COLOR_NEUTRAL


def _lifecycle(order: dict) -> tuple[str, str, int]:
    """(marker, headline word, embed color). The marker and word carry the lifecycle, the color
    carries the cash direction — two independent facts about the order. Falls back to the
    Bought/Sold verb when the legs sit on both sides — an honest "something happened" beats guessing
    at a roll."""
    color = _stripe_color(order)
    match _open_close(order):
        case "OPEN":
            return "➕", "OPEN", color
        case "CLOSE":
            return "➖", "CLOSE", color
        case _:
            return "\U0001f501", _direction(order), color


def _is_opening(order: dict) -> bool:
    return _open_close(order) == "OPEN"


def _underlying_symbols(order: dict) -> list[str]:
    seen = []
    for leg in order.get("order_legs") or []:
        sym = leg.get("underlying_symbol")
        if sym and sym not in seen:
            seen.append(str(sym))
    return seen


def _underlyings(order: dict) -> str:
    return "/".join(_underlying_symbols(order))


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _trim(value) -> str:
    """A number without trailing zeros: 122.0 -> '122', 457.5 -> '457.5'."""
    n = _num(value)
    return f"{n:g}" if n is not None else str(value)


def _quantity(order: dict) -> str:
    """Contract count as '2×'. The largest leg quantity, so a ratio spread reports its widest side
    rather than understating the position. An all-equity order counts in shares, not contracts —
    '100×' on a stock trade would read as a 100-lot, a 100x overstatement of the position."""
    legs = order.get("order_legs") or []
    sizes = [q for q in (_num(leg.get("quantity")) for leg in legs) if q]
    if not sizes:
        return ""
    # The feed tags equity legs "S", not "E" — checking only "E" sent every stock trade down the
    # contracts branch and printed the exact "100×" this guard exists to prevent. Both accepted so a
    # future "E" doesn't silently regress it.
    equity = bool(legs) and all(str(leg.get("asset_type") or "").upper() in ("E", "S") for leg in legs)
    return f"{max(sizes):g} sh" if equity else f"{max(sizes):g}×"


def _leg_body(leg: dict) -> str:
    """One leg as strike+right, e.g. '122P'. Falls back to the raw OCC symbol for anything that
    isn't a plain option leg (equity legs carry no strike)."""
    strike, right = leg.get("strike_price"), leg.get("call_or_put")
    if strike is None or not right:
        return str(leg.get("symbol") or leg.get("underlying_symbol") or "?")
    return f"{_trim(strike)}{right}"


def _leg_sign(leg: dict) -> str:
    """'-' short, '+' long, '' when the feed didn't say — the most load-bearing character on the line.

    Without it a vertical is unreadable: '122P/117P' is a CREDIT spread short the 122 and a DEBIT
    spread long it, and the net db/cr can't settle it (buying back a credit spread is a debit). A
    lone option has the same problem — "Option" says nothing about the side taken. The feed carries
    the answer per leg in `action` ("selltoopen"/"buytoclose"/...); we were dropping it."""
    action = str(leg.get("action") or "").lower()
    if action.startswith("sell"):
        return "-"
    if action.startswith("buy"):
        return "+"
    return ""


def _fmt_leg(leg: dict) -> str:
    return f"{_leg_sign(leg)}{_leg_body(leg)}"


def _common_sign(order: dict) -> str:
    """The one sign shared by every leg, or '' if they disagree or the feed didn't say."""
    signs = {_leg_sign(leg) for leg in order.get("order_legs") or []}
    return signs.pop() if len(signs) == 1 else ""


def _is_futures_leg(leg: dict) -> bool:
    if str(leg.get("asset_type") or "") == "/":  # the feed's own tag, and the authority
        return True
    # Backstop for a leg the feed left untagged. It has to exclude options explicitly: a futures
    # OPTION carries a slash-prefixed underlying too, and it quotes in cash like any other option,
    # so a bare startswith("/") would strip the db/cr off a trade that had genuinely earned it.
    if leg.get("strike_price") is not None or leg.get("call_or_put"):
        return False
    return str(leg.get("underlying_symbol") or "").startswith("/")


def _is_futures(order: dict) -> bool:
    """An outright futures order. Only when EVERY leg is one — anything mixed still quotes in cash."""
    legs = order.get("order_legs") or []
    return bool(legs) and all(_is_futures_leg(leg) for leg in legs)


def _legs_summary(order: dict, max_legs: int = 6) -> str:
    legs = order.get("order_legs") or []
    if not legs:
        return ""
    # An equity leg's body IS its underlying, which the line already carries — rendering both gave
    # "NVTS NVTS". Compare the BODY, not the signed token, or a sign defeats the check.
    underlyings = _underlyings(order)
    tokens = [_fmt_leg(leg) for leg in legs[:max_legs] if _leg_body(leg) not in underlyings]
    if not tokens:
        return ""
    shown = "/".join(tokens)
    if len(legs) > max_legs:
        shown += f"/+{len(legs) - max_legs}"
    return shown


def _structure(order: dict) -> str:
    """Size and legs together: '2× -457.5P/+485P/-512.5P'. Either half alone is fine.

    When no leg token survives — stock and futures, whose body is just the underlying the line
    already carries — the size takes the sign instead, so '-1×' still says which way they went.
    Without that a futures fill showed its side nowhere at all once the price suffix was dropped."""
    qty, legs = _quantity(order), _legs_summary(order)
    if qty and not legs:
        qty = _common_sign(order) + qty
    return " ".join(p for p in (qty, legs) if p)


def _date_label(iso: str) -> str:
    """'2026-08-07' -> 'Aug 7'. The year is dropped: everything in this feed is near-dated. Falls
    back to the raw string on anything unparseable."""
    try:
        y, m, d = (int(part) for part in str(iso).split("-")[:3])
        return f"{_MONTHS[m - 1]} {d}"
    except (ValueError, IndexError):
        return str(iso)


def _expiries(order: dict) -> list[str]:
    return sorted(
        {str(leg["expiration_date"]) for leg in order.get("order_legs") or [] if leg.get("expiration_date")}
    )


def _expiry(order: dict) -> str:
    """'Aug 7', or 'Aug 5–Aug 12' for a calendar. '0DTE' is called out because same-day expiry is a
    different animal from anything else here."""
    exps = _expiries(order)
    if not exps:
        return ""
    label = _date_label(exps[0]) if len(exps) == 1 else f"{_date_label(exps[0])}–{_date_label(exps[-1])}"
    filled = str(order.get("executed_at") or order.get("filled_at") or "")[:10]
    if len(exps) == 1 and filled and exps[0] == filled:
        label += " · 0DTE"
    return label


def _filled_at(order: dict) -> datetime | None:
    """When the order actually filled, as an aware datetime. `executed_at` is the platform's own
    fill time; `filled_at` is the backstop."""
    for key in ("executed_at", "filled_at"):
        raw = str(order.get(key) or "")
        if not raw:
            continue
        try:  # fromisoformat only learned to parse a trailing 'Z' in 3.11
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def _price(order: dict) -> str:
    price = order.get("price_string") or order.get("price")
    if price in (None, ""):
        return ""
    # A futures price is a quoted LEVEL, not cash changing hands — nobody pays $29,728.75 to buy one
    # MNQ. Labelling it "cr" put a five-figure "credit" next to a $1.46 butterfly and read as a
    # windfall. The side moves onto the size instead (see `_structure`), which is what db/cr was
    # really conveying on those rows.
    if _is_futures(order):
        return f"${price}"
    # Otherwise db/cr comes from the direction, not just order_type — a "limit" single-leg order has
    # no net_* type but is still unambiguously a debit or a credit, and the feed's UI labels it so.
    suffix = {"Bought": "db", "Sold": "cr"}.get(_direction(order), "")
    return f"${price} {suffix}".strip()


def _spot(order: dict) -> str:
    """Underlying price at the fill, e.g. 'PLTR 163.86'. Strikes mean nothing without it: a 122P
    closed for a penny is only legible once you can see the stock at 163.86."""
    value = order.get("underlying_price_string") or order.get("underlying_price")
    syms = _underlying_symbols(order)
    if value in (None, "") or not syms:
        return ""
    # Take the first underlying from the LIST, never by splitting the joined string on "/" — a
    # futures symbol is itself slash-prefixed ("/MNQU6"), so that split yielded an empty symbol and
    # every futures row printed a bare price with a doubled separator.
    sym = syms[0]
    n = _num(value)
    return f"{sym} {n:.2f}" if n is not None else f"{sym} {value}"


def _iv_rank(order: dict) -> str:
    n = _num(order.get("tos_iv_rank"))
    return f"{n:.0f}" if n is not None else ""


def _pop(order: dict) -> str:
    """Probability of profit — meaningful on an opening trade, noise on an exit, so it is only ever
    shown on an open."""
    n = _num(order.get("probability_of_profit"))
    return f"{n:.0f}%" if n is not None and _is_opening(order) else ""


def _tags(order: dict) -> list[str]:
    return [
        name
        for name, on in (
            ("Earnings", order.get("is_earnings_play")),
            ("Hedge", order.get("is_hedge")),
            ("Scalp", order.get("is_scalp_trade")),
        )
        if on
    ]


def _rationale(order: dict, limit: int = 220) -> list[str]:
    """The trader's own words, when they left any — the most useful part of the feed, and the reason
    this is worth a push rather than a dashboard panel.

    Two independent fields carry it and neither subsumes the other: `comments` is a thread hung off
    the order, `reason` is a single string attached to the order itself. Reading only `comments` — as
    this did — dropped the rationale on roughly a third of the feed: measured on one live pull,
    `reason` was populated on 23 of 50 orders against `comments`' 7. On a CLOSE it is usually the
    whole point of the card ("Closed for 50% gain", "Cutting losses at ~20%"), so both are rendered
    when both are present. An exact repeat is shown once; each is truncated on its own."""
    out: list[str] = []
    seen: set[str] = set()
    candidates: list[str] = []
    for c in order.get("comments") or []:
        body = str(c.get("body") or "").strip()
        if body:  # the first non-empty comment only — a long thread would drown the card
            candidates.append(body)
            break
    candidates.append(str(order.get("reason") or "").strip())
    for text in candidates:
        if not text:
            continue
        key = " ".join(text.lower().split())
        if key in seen:
            continue
        seen.add(key)
        out.append(text if len(text) <= limit else text[: limit - 1] + "…")
    return out


def _trader_name(order: dict, trader_names: dict[int, str]) -> str:
    try:
        trader_id = int(order.get("trader_id"))
    except (TypeError, ValueError):
        trader_id = -1
    return trader_names.get(trader_id, f"trader {order.get('trader_id', '?')}")


def format_order(order: dict, trader_names: dict[int, str]) -> str:
    """One order as plain text — the log floor and any non-Discord channel read this.

    Three lines: who and what, the numbers, the trader's comment. Kept UTC (plain text has no way to
    render in the reader's timezone; the Discord embed does that properly)."""
    marker, word, _ = _lifecycle(order)
    strategy = str(order.get("strategy") or "order")
    head = f"{marker} {_trader_name(order, trader_names)} · {word} {_underlyings(order)} {strategy}".replace(
        "  ", " "
    )

    when = _filled_at(order)
    detail = [
        _structure(order),
        f"exp {_expiry(order)}" if _expiry(order) else "",
        _price(order),
        _spot(order),
        f"IVR {_iv_rank(order)}" if _iv_rank(order) else "",
        f"POP {_pop(order)}" if _pop(order) else "",
        *_tags(order),
        when.astimezone(timezone.utc).strftime("%H:%M UTC") if when else "",
    ]
    lines = [head, " · ".join(p for p in detail if p)]
    lines.extend(f"> {note}" for note in _rationale(order))
    return "\n".join(ln for ln in lines if ln)


def _embed_field(name: str, value: str) -> dict | None:
    return {"name": name, "value": value, "inline": True} if value else None


def build_embed(order: dict, trader_names: dict[int, str]) -> dict:
    """One order as a Discord embed: a colored card with the comment as the body and the numbers as
    labeled fields, three to a row.

    `timestamp` is the fill time — Discord renders it in each reader's own timezone, which is the
    honest way to show a time on a message that may arrive well after the fact."""
    _, word, color = _lifecycle(order)
    strategy = str(order.get("strategy") or "order")
    title = " · ".join(p for p in (word, f"{_underlyings(order)} {strategy}".strip()) if p)

    fields = [
        f
        for f in (
            _embed_field("Trade", _structure(order)),
            _embed_field("Price", _price(order)),
            _embed_field("Expiry", _expiry(order)),
            _embed_field("Spot", _spot(order)),
            _embed_field("IV rank", _iv_rank(order)),
            _embed_field("POP", _pop(order)),
        )
        if f
    ]

    embed: dict = {
        "author": {"name": _trader_name(order, trader_names)},
        "title": title[:256],
        "color": color,
        "fields": fields,
    }
    notes = _rationale(order)
    if notes:
        embed["description"] = "\n".join(f"> {n}" for n in notes)[:4096]
    if _tags(order):
        embed["footer"] = {"text": " · ".join(_tags(order))}
    when = _filled_at(order)
    if when:
        embed["timestamp"] = when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return embed


# --------------------------------------------------------------------------- entrypoint
def _sort_key(order: dict) -> tuple:
    """Chronological, so a multi-order run reads in the order the trades happened. `executed_at` is
    the platform's own timestamp; `id` breaks ties and covers a null."""
    return (str(order.get("executed_at") or ""), int(order.get("id") or 0))


def run(cfg: dict | None = None) -> dict:
    cfg = cfgmod.load_config() if cfg is None else cfg  # an explicit {} must stay {}, not fall back
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
                embed=build_embed(order, trader_names),
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
