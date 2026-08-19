"""The one card grammar for third-party trade feeds (lossdog VIP, tastylive Follow).

Both feeds post into the same Discord channel, and until 2026-08-18 they rendered through two
renderers kept in lockstep by discipline — same silhouette, separate code. This module is that
silhouette made into code: each feed notifier maps its own feed's JSON (with all its verified
quirks) into the neutral card spec below, and everything after the spec — the embed, the
plain-text floor, the webhook identity — is built here, once. Uniformity by construction.

Three facts are legible by color on every Discord post (the user's scheme, chosen from mockups):
  - **Service** — the webhook identity: each card posts under its service's own name and logo
    (`identity()` → `Notifier.notify(identity=...)`), so the message row says lossdog or
    tastylive before the card even renders.
  - **Cash** — the embed stripe, unchanged meaning from both feeds' history: green took money
    in, red paid it out, slate is a futures level (a quoted price, not cash changing hands) or
    a direction the feed didn't state.
  - **Lifecycle** — a colored square leading the title: 🟩 OPEN · ⬜ CLOSE · 🟨 ROLL · 🟦 FUTURES.
    Always beside the word, never instead of it — color is never the only carrier of a fact.

The plain-text floor (`format_text`) is the canonical record and has no color at all: service is
a bracket tag, lifecycle is the ➕/➖/🔁 marker plus the word, cash stays the cr/db suffix. A
channel that can't render a card loses nothing but layout.

The card spec (a plain dict; every field optional, missing pieces drop out of the render):
  service        "lossdog" | "tastylive" — keys the identity and the text tag
  trader         {"name", "subtitle", "icon_url"} — the human on the embed's author row
  lifecycle      "OPEN" | "CLOSE" | "ROLL" | "FUTURES" | any fallback word | ""
  symbol         underlying(s), e.g. "TSLA"
  strategy       "Short Call", "Iron Condor", ...
  structure      size + signed legs, e.g. "1× -61C"
  price          "$1.50 cr", "$23,891.50" (a futures level carries no suffix)
  cash           "credit" | "debit" | "level" | "" — drives the stripe, nothing else
  expiry         "18 Sep 26 · 31 DTE", "Aug 22 · 0DTE", "21 Aug 26 – 16 Oct 26"
  context_extra  list — anything else that situates the trade (the follow feed's spot price)
  stats          list — what the feed measures ("IVR 25", "POP 34%") or is ("Options", "2 legs")
  body           list of lines — the trader's rationale, or the per-leg detail
  body_quoted    True to render body lines as Discord quotes ("> ...")
  body_in_embed  False keeps the body off the CARD only (it always stays on the text floor,
                 which is the canonical record) — for a body that would repeat the fields
  footer         list — feed name and any provenance note (late-sync warning)
  note           one extra plain-text line (the text floor's copy of that warning)
  timestamp      aware datetime of the fill/execution, or None
  url            link target for the title, or None
"""

from __future__ import annotations

from datetime import timezone
from typing import Any

# Cash direction → embed stripe. The green/red pair is what both feeds have always meant by it;
# slate is the follow feed's futures/unknown neutral, now shared: an unknown direction reads as
# "the feed didn't say", never as a guessed debit.
COLOR_CREDIT = 0x22C55E  # green — money came in
COLOR_DEBIT = 0xEF4444  # red — money went out
COLOR_NEUTRAL = 0x6B7280  # slate — a futures level, or a direction the feed didn't state

_STRIPES = {"credit": COLOR_CREDIT, "debit": COLOR_DEBIT}

# Lifecycle → the colored square leading the embed title. Chosen so none of them collide with the
# stripe's meaning: green/red stay cash, so OPEN gets its own green *square* only because open-vs-
# close is what the user scans for and the square sits beside the word that names it.
_TITLE_MARKS = {"OPEN": "\U0001f7e9", "CLOSE": "⬜", "ROLL": "\U0001f7e8", "FUTURES": "\U0001f7e6"}

# Lifecycle → the plain-text floor's marker (pre-color history, kept: ➕ / ➖ / 🔁, 🔷 for futures).
_TEXT_MARKS = {"OPEN": "➕", "CLOSE": "➖", "ROLL": "\U0001f501", "FUTURES": "\U0001f537"}
_TEXT_MARK_FALLBACK = "\U0001f501"  # a lifecycle word we don't recognize still gets a marker

# Service → webhook identity. The avatar defaults are each service's own YouTube channel picture
# (a stable, hotlinkable PNG at a known size — the services' sites serve .ico, which Discord's
# avatar renderer doesn't take). Override via the notify config's `feed_avatars` (avatar only —
# the username IS the service label and stays).
_IDENTITIES = {
    "lossdog": {
        "username": "Lossdog VIP",
        "avatar_url": (
            "https://yt3.googleusercontent.com/"
            "r0GC_zAI7Eov1LrsY3flbJFzMIsMK8THhMJEBFgdvoELBmPyRKsADPW-O7mszFmrLuF7eZuOuQ"
            "=s160-c-k-c0x00ffffff-no-rj"
        ),
    },
    "tastylive": {
        "username": "tastylive Follow",
        "avatar_url": (
            "https://yt3.googleusercontent.com/"
            "0xOp_6WxkIS4ldyiUKJa-Y85gETkB23HMNbjeYlVZ74xSiHbzdY2VGFNcojXmGHnhxXU1MmZoA"
            "=s160-c-k-c0x00ffffff-no-rj"
        ),
    },
}


def identity(spec: dict[str, Any], avatars: dict[str, Any] | None = None) -> dict[str, str] | None:
    """The `{"username", "avatar_url"}` webhook override for this card's service, or None for a
    service we don't know (the post then keeps the webhook's configured name — degraded, not
    wrong). `avatars` is the notify config's `feed_avatars` mapping of service → image URL."""
    base = _IDENTITIES.get(str(spec.get("service") or ""))
    if not base:
        return None
    out = dict(base)
    override = (avatars or {}).get(spec.get("service"))
    if override:
        out["avatar_url"] = str(override)
    return out


def _join(parts: list[Any] | None) -> str:
    return " · ".join(str(p) for p in (parts or []) if p)


def _embed_field(name: str, value: str) -> dict | None:
    return {"name": name, "value": value, "inline": True} if value else None


def _title(spec: dict[str, Any], *, mark: bool) -> str:
    word = str(spec.get("lifecycle") or "")
    head = " · ".join(
        p for p in (word, f"{spec.get('symbol') or ''} {spec.get('strategy') or ''}".strip()) if p
    )
    glyph = _TITLE_MARKS.get(word, "") if mark else ""
    return f"{glyph} {head}".strip()


def build_embed(spec: dict[str, Any]) -> dict:
    """One card spec as a Discord embed: 🟩-marked lifecycle title, Trade / Context / Stats inline
    so the numbers sit in one horizontal strip, the body under them, the stripe from the cash
    direction. `timestamp` is sent as UTC — Discord renders it in each reader's own timezone,
    which is the honest way to show a time on a message that may arrive well after the fact."""
    trader = spec.get("trader") or {}
    author: dict = {
        "name": _join([trader.get("name"), trader.get("subtitle")]) or "trader",
    }
    if trader.get("icon_url"):
        author["icon_url"] = str(trader["icon_url"])

    fields = [
        f
        for f in (
            _embed_field("Trade", _join([spec.get("structure"), spec.get("price")])),
            _embed_field("Context", _join([spec.get("expiry"), *(spec.get("context_extra") or [])])),
            _embed_field("Stats", _join(spec.get("stats"))),
        )
        if f
    ]

    embed: dict = {
        "author": author,
        "title": _title(spec, mark=True)[:256],
        "color": _STRIPES.get(str(spec.get("cash") or ""), COLOR_NEUTRAL),
        "fields": fields,
    }
    if spec.get("url"):
        embed["url"] = str(spec["url"])
    body = [str(b) for b in (spec.get("body") or []) if b]
    if body and spec.get("body_in_embed", True):
        prefix = "> " if spec.get("body_quoted") else ""
        embed["description"] = "\n".join(f"{prefix}{line}" for line in body)[:4096]
    footer = _join(spec.get("footer"))
    if footer:
        embed["footer"] = {"text": footer}
    when = spec.get("timestamp")
    if when is not None:
        embed["timestamp"] = when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return embed


def format_text(spec: dict[str, Any]) -> str:
    """One card spec as plain text — the log floor and any non-Discord channel read this. Head
    line, numbers line, body lines quoted, one optional note. Kept UTC (plain text has no way to
    render in the reader's timezone; the Discord embed does that properly)."""
    word = str(spec.get("lifecycle") or "")
    marker = _TEXT_MARKS.get(word, _TEXT_MARK_FALLBACK if word else "")
    service = str(spec.get("service") or "")
    trader = (spec.get("trader") or {}).get("name") or "trader"
    head = " ".join(
        p
        for p in (
            marker,
            f"[{service}]" if service else "",
            f"{trader} ·",
            word,
            f"{spec.get('symbol') or ''} {spec.get('strategy') or ''}".strip(),
        )
        if p
    )

    when = spec.get("timestamp")
    detail = [
        spec.get("structure"),
        f"exp {spec['expiry']}" if spec.get("expiry") else "",
        spec.get("price"),
        *(spec.get("context_extra") or []),
        *(spec.get("stats") or []),
        when.astimezone(timezone.utc).strftime("%H:%M UTC") if when is not None else "",
    ]
    lines = [head, _join(detail)]
    lines.extend(f"> {b}" for b in (spec.get("body") or []) if b)
    if spec.get("note"):
        lines.append(str(spec["note"]))
    return "\n".join(ln for ln in lines if ln)
