"""Post a session's QuikOptions Hot Options Report to Discord, in one of four styles.

The styles (`quikoptions.post_style`, docs/quikoptions-plan.md):

    cards    a header embed (title, date, the day in four fields, the capture time in its footer),
             then the chosen cards as images, two to a message so Discord shows them side
             by side
    singles  the same header, then one card image per message
    embed    one embed and no images: the summary, then each chosen section as a small table
    text     one plain message (split at a section if it would pass Discord's 2,000 characters):
             the summary and each section as a small monospace table

The sections (`quikoptions.post_cards`) are the Options flow `today` cards by title (Derived flow,
Net by name, Trades — the site's Birdseye, Names across tables, Largest by contracts, Top sweeps,
Top spreads, Vol / OI) plus `Events`: the session's high-impact releases with actual against
estimate, and the next ones, from the calendar capture. In the image styles Events is a capture of
the post page's table. Default: Derived flow (the top scored flows, from
`scripts/quikoptions_flow.py`), Trades and Events, one screen capture a message (chosen 2026-10-03;
the others stay configurable). The day does not need every table told.

The images are the console's own Options flow cards, captured with `tools/ui-check.mjs --card`,
which refuses rather than crops the wrong thing: every card is titled "<name> — <session>", so a
page still showing another day fails the run instead of posting it under this one's header. Every
image is captured before anything is posted, so a capture failure never leaves a header without its
pictures. Every figure in text comes from the saved capture, never from a picture.

A script, not a package: it drives a browser and pushes a webhook, so a failure costs a post and
never a capture. It posts to the series' own webhook (`cherrypick secrets-set --channel
discord_quikoptions`, `post_webhook: "dedicated"`) or to the suite's Discord notify webhook
(`post_webhook: "notify"`): a choice, never a fallback. With the chosen one not stored it posts
nothing and says why. Every message goes out with Discord's mentions switched off, so a title or
caption can never ping anyone.

Once per session: `state/quikoptions-post.json` (written only here, only after a message landed)
records which messages of which session went out, and the title, style and sections they went out
under. A re-run sends only what is missing, in order, the same way, so a failure halfway resumes
rather than repeating, and one day never carries two titles. It posts only a capture that passed the
page's own checks; with no capture for the session it posts nothing.

    python scripts/quikoptions_post.py [--session YYYY-MM-DD] [--webhook notify|dedicated]
                                       [--style cards|singles|embed|text] [--cards "A,B"]
                                       [--title TEXT] [--dry-run] [--force] [--keep DIR]

`--style`, `--cards`, `--title` and `--webhook` override the config for one run (trying styles).

Two more posts, both narrow text (decided 2026-10-03): `--kind morning`, after the open-interest
check, says how the last session's flows came out (opened, closed, mixed) beside their first scores;
`--kind weekly`, on Fridays, is the scorecard — the week's checks, confirmations and how the calls
did a day on, descriptive only until the fixed 40-session test.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import home as _home

REPO = Path(__file__).resolve().parents[1]
UI_CHECK = REPO / "packages" / "console" / "tools" / "ui-check.mjs"
CONSOLE = REPO / "packages" / "console"
ET = ZoneInfo("America/New_York")
MARKER_KEEP = 30
USER_AGENT = "cherrypick-notifier/1.0 (+https://github.com/cherrypick)"  # Discord's front refuses urllib's
PAUSE_S = 2.5
EMBED_COLOUR = 0xD23F57  # the console's accent
CARDS_PER_MESSAGE = 2
ROWS = 5  # rows per section in the text styles: a glance, not the table
TEXT_LIMIT = 1900  # Discord caps a message at 2,000 characters
EVENTS = "Events"
NO_MENTIONS = {"parse": []}
TABLE_WORD = {"outrights": "outright", "sweeps": "sweep", "spreads": "spread"}


def _now_et() -> datetime:
    return datetime.now(ET)


def _log(line: str) -> None:
    stamped = f"{_now_et().isoformat(timespec='seconds')} {line}"
    try:
        print(stamped)
    except (OSError, UnicodeError):
        pass
    try:
        path = _home.logs_dir("quikoptions") / "post.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(stamped + "\n")
    except OSError:
        pass  # a log line is never worth a failed post


# ------------------------------------------------------------------------------------------------
# The capture and the settings.
# ------------------------------------------------------------------------------------------------


def capture_path(session: str) -> Path:
    return _home.data_dir("quikoptions") / "hot-options" / f"{session}.json"


def calendar_path(captured: str) -> Path:
    return _home.data_dir("quikoptions") / "calendar" / f"{captured}.json"


def load_capture(session: str) -> dict | None:
    """The saved capture for `session`, or None. A `.rejected` capture is a different file, so it is
    never read here: only a capture that passed the page's own checks is posted."""
    try:
        doc = json.loads(capture_path(session).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = isinstance(doc, dict) and doc.get("session") == session and not doc.get("problems")
    return doc if ok else None


def load_flow(session: str) -> dict | None:
    """The session's scored derived flows (`scripts/quikoptions_flow.py score`), or None."""
    try:
        doc = json.loads(
            (capture_path(session).with_name(f"{session}.flow.json")).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def load_calendar(session: str) -> dict | None:
    """The calendar captured on `session` (the same run as the report), if it passed its checks."""
    try:
        doc = json.loads(calendar_path(session).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and not doc.get("problems") else None


def post_settings(cfg: dict) -> dict:
    """{post, problem, title, webhook, cards, style}. The raw values are checked here, not the
    settings' fallbacks: a broken title, an unknown webhook, style or section refuses the run and
    says why, rather than posting something nobody chose."""
    from cherrypick.orchestrator import config as cfgmod

    q = cfg.get("quikoptions") or {}
    settings = cfgmod.quikoptions_settings(cfg)
    raw = q.get("post_title", "Hot options")
    return {
        "post": settings["post"],
        "problem": cfgmod.quikoptions_title_problem(raw) or cfgmod.quikoptions_post_problem(q),
        "title": str(raw).strip(),
        "webhook": settings["post_webhook"],
        "cards": settings["post_cards"],
        "style": settings["post_style"],
    }


# ------------------------------------------------------------------------------------------------
# Words and small tables. Pure.
# ------------------------------------------------------------------------------------------------


def _dollars(v: float | None) -> str:
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e9:
        return f"${a / 1e9:.2f}B"
    if a >= 1e6:
        return f"${a / 1e6:.2f}M"
    if a >= 1e3:
        return f"${a / 1e3:.0f}K" if a >= 1e5 else f"${a / 1e3:.1f}K"
    return f"${a:.0f}"


def _count(v: float | None) -> str:
    return "—" if v is None else f"{v:,.0f}"


def _num(v: float | None, digits: int = 2) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def contract(expires: str | None, strike: float | None, cp: str | None) -> str:
    """`15 Jan 27 16C`, the way the console names a contract."""
    try:
        exp = date.fromisoformat(expires or "").strftime("%d %b %y")
    except ValueError:
        exp = "—"
    k = "—" if strike is None else f"{strike:g}"
    return f"{exp} {k}{'C' if cp == 'call' else 'P' if cp == 'put' else ''}"


def _side(row: dict) -> str:
    s = row.get("side") or {}
    if not s.get("sentiment"):
        return "—"
    return f"{s['sentiment'].lower()} {s.get('fill', '').lower()}".strip()


def _table(rows: list[list[str]], right: set[int]) -> str:
    """Rows of cells as an aligned monospace block; columns in `right` are right-aligned."""
    if not rows:
        return "(none)"
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = []
    for r in rows:
        cells = [
            c.rjust(w) if i in right else c.ljust(w) for i, (c, w) in enumerate(zip(r, widths, strict=True))
        ]
        lines.append("  ".join(cells).rstrip())
    return "\n".join(lines)


def _captured(doc: dict) -> str:
    try:
        return f"{datetime.fromisoformat(doc.get('saved_at') or '').astimezone(ET):%H:%M} ET"
    except ValueError:
        return ""


def summary(doc: dict) -> dict:
    """The day in four lines, every figure from the capture: {most, largest, sides, names}."""
    tables = doc.get("tables") or {}
    derived = doc.get("derived") or {}
    top = (tables.get("birdseye") or [None])[0]
    most = f"{top['symbol']} · {(top.get('shown') or {}).get('total', '—')} trades" if top else "—"
    big = derived.get("largest_trade")
    largest = "—"
    if big:
        row = next((r for r in tables.get(big["table"], []) if r.get("symbol") == big.get("symbol")), {})
        side = (row.get("side") or {}).get("sentiment") or row.get("direction")
        largest = (
            f"{big.get('symbol')} {_dollars(big['premium'])} {TABLE_WORD.get(big['table'], big['table'])}"
        )
        largest += f" ({str(side).lower()})" if side else ""
    by_side = derived.get("premium_by_side") or {}
    n_side = derived.get("trades_by_side") or {}

    def side(name: str) -> str:
        return f"{_dollars(by_side.get(name))} ({n_side.get(name, 0)})" if name in by_side else "—"

    names = " · ".join(n["symbol"] for n in derived.get("names") or []) or "none"
    return {
        "most": most,
        "largest": largest,
        "sides": f"{side('Bullish')} / {side('Bearish')}",
        "names": names,
    }


def _trade_cells(r: dict) -> list[str]:
    size = f"{_count(r.get('size'))} @ {_num(r.get('price'))}"
    return [
        r["symbol"],
        contract(r.get("expires"), r.get("strike"), r.get("cp")),
        size,
        _dollars(r.get("premium")),
        _side(r),
    ]


def _spread_cells(r: dict) -> list[str]:
    symbol = r["symbol"] + (" ⛓" if r.get("group") else "")
    return [
        symbol,
        r.get("spread") or "—",
        _count(r.get("size")),
        r.get("direction") or "—",
        _dollars(r.get("premium")),
    ]


def _voloi_cells(r: dict) -> list[str]:
    exp = contract(r.get("expires"), r.get("strike"), r.get("cp"))
    return [r["symbol"], exp, _count(r.get("volume")), _count(r.get("oi")), f"{_num(r.get('v_oi'), 1)}x"]


def _flow_cells(f: dict) -> list[str]:
    s = f.get("confirmed_score") if f.get("confirmed_score") is not None else f.get("score")
    read = f"{f.get('direction') or '?'} {f.get('view') or ''}".strip()
    return [
        f"{s:+.0f}" if s is not None else "—",
        f["symbol"],
        f.get("what") or "",
        read,
        _dollars(f.get("delta_dollars")),
    ]


def derived_text(flow: dict | None) -> str:
    """The top scored flows, then the most bullish and bearish names by net delta dollars."""
    if flow is None:
        return "(not scored yet)"
    lines = [_table([_flow_cells(f) for f in (flow.get("flows") or [])[:ROWS]], {0, 4})]
    names = flow.get("names") or []
    bull = sorted((n for n in names if n.get("net", 0) > 0), key=lambda n: -n["net"])[:3]
    bear = sorted((n for n in names if n.get("net", 0) < 0), key=lambda n: n["net"])[:3]
    if bull or bear:
        lines.append(
            "net Δ$: "
            + " · ".join(
                f"{n['symbol']} {'+' if n['net'] > 0 else '−'}{_dollars(n['net'])}" for n in bull + bear
            )
        )
    return "\n".join(lines)


def section_text(name: str, doc: dict, calendar: dict | None) -> str:
    """One section as a small monospace table (or a line), at most ROWS rows: the text styles."""
    t = doc.get("tables") or {}
    if name in ("Largest by contracts", "Top sweeps"):
        key = "outrights" if name == "Largest by contracts" else "sweeps"
        return _table([_trade_cells(r) for r in (t.get(key) or [])[:ROWS]], {2, 3})
    if name == "Top spreads":
        return _table([_spread_cells(r) for r in (t.get("spreads") or [])[:ROWS]], {2, 4})
    if name == "Vol / OI":
        return _table([_voloi_cells(r) for r in (t.get("voloi") or [])[:ROWS]], {2, 3, 4})
    if name in ("Trades", "Birdseye"):
        rows = []
        for r in (t.get("birdseye") or [])[:ROWS]:
            share = r.get("call_share")
            big = (r.get("bands") or {}).get("100+")
            rows.append(
                [
                    r["symbol"],
                    f"{(r.get('shown') or {}).get('total', '—')} trades",
                    "—" if share is None else f"{share:.0%} calls",
                    f"{_count(big)} of 100+",
                ]
            )
        return _table(rows, {1, 2, 3})
    if name == "Names across tables":
        names = (doc.get("derived") or {}).get("names") or []
        return "\n".join(f"{n['symbol']:<6} {' · '.join(n['tables'])}" for n in names) or "(none)"
    if name == EVENTS:
        return calendar_text(doc["session"], calendar)
    if name in ("Derived flow", "Net by name"):
        return derived_text(doc.get("_flow"))
    return "(unknown section)"


def calendar_text(session: str, calendar: dict | None) -> str:
    """The session's high-impact releases with actual against estimate, then the next ones."""
    if calendar is None:
        return "(no calendar captured with this session)"
    events = [e for e in calendar.get("events") or [] if e.get("impact") == "H" and e.get("date")]
    today = [e for e in events if e["date"] == session]
    ahead_end = (date.fromisoformat(session) + timedelta(days=7)).isoformat()
    ahead = [e for e in events if session < e["date"] <= ahead_end]
    lines = []
    for e in today[:ROWS]:
        detail = (
            f"{e.get('actual') or '—'} vs {e.get('estimate') or '—'} est" if e.get("actual") else "pending"
        )
        lines.append(f"{e.get('time_et') or '':>5}  {e['event']}: {detail}")
    if ahead:
        if lines:
            lines.append("next:")
        seen = set()
        for e in ahead:
            key = (e["date"], e["event"].split(" (")[0])
            if key in seen or len(seen) >= ROWS:
                continue
            seen.add(key)
            when = date.fromisoformat(e["date"])
            lines.append(f"{when:%a} {when.day:>2} {e.get('time_et') or '':>5}  {e['event']}")
    return "\n".join(lines) or "(no high-impact events)"


def _title_line(title: str, session: date) -> str:
    return f"{title} — {session:%a} {session.day} {session:%b %Y}"


def _footer(doc: dict) -> str:
    captured = _captured(doc)
    # No source attribution (decided 2026-10-03): the footer is the capture time alone.
    return f"Captured {captured}." if captured else "Capture time not recorded."


# ------------------------------------------------------------------------------------------------
# The message plan: what each style sends. Pure.
# ------------------------------------------------------------------------------------------------


def _net_names(flow: dict, sign: int, n: int = 3) -> str:
    names = [x for x in flow.get("names") or [] if (x.get("net") or 0) * sign > 0]
    names.sort(key=lambda x: -abs(x["net"]))
    return (
        " · ".join(f"{x['symbol']} {'+' if sign > 0 else '−'}{_dollars(x['net'])}" for x in names[:n]) or "—"
    )


def _shown(f: dict) -> float | None:
    return f.get("confirmed_score") if f.get("confirmed_score") is not None else f.get("score")


def yesterday_line(prev: dict | None, n: int = ROWS) -> str | None:
    """The previous session's top calls, one day on: each name's move and whether it went the call's
    way. None until that session has a 1-day outcome (recorded by the next day's scoring)."""
    returns = (((prev or {}).get("outcomes") or {}).get("1d") or {}).get("returns") or {}
    if not returns:
        return None
    out = []
    for f in (prev.get("flows") or [])[:n]:
        move, s = returns.get(f["symbol"]), _shown(f)
        if move is None or s is None:
            continue
        mark = "✓" if (move > 0) == (s > 0) and move != 0 else "✗" if move != 0 else "·"
        out.append(f"{f['symbol']} {s:+.0f} {move:+.1%} {mark}")
    return " · ".join(out) or None


def header_embed(
    doc: dict, title: str, calendar: dict | None = None, with_calendar: bool = False, prev: dict | None = None
) -> dict:
    """The image styles' first message: the title, the date and the day in a few fields — led by
    the derived flow once it is scored (the names by net delta dollars, the top flow, the largest
    trade with no read, yesterday's calls one day on), else the site's own summary; Events when it is
    a chosen section. The date is always in the title line and the capture time in the footer,
    whatever the title, so no title can drop them."""
    flow = doc.get("_flow")
    if flow and flow.get("flows"):
        top = flow["flows"][0]
        fields = [
            {"name": "Bullish", "value": _net_names(flow, +1), "inline": False},
            {"name": "Bearish", "value": _net_names(flow, -1), "inline": False},
            {
                "name": "Top flow",
                "value": " ".join(
                    x
                    for x in (f"{_shown(top):+.0f}", top["symbol"], top.get("what"), top.get("direction"))
                    if x
                ),
                "inline": True,
            },
        ]
        if flow.get("unread"):
            u = flow["unread"][0]
            fields.append(
                {
                    "name": "Unread, largest",
                    "value": f"{u['symbol']} {u.get('what') or ''} "
                    f"({_dollars(u.get('delta_dollars') or u.get('premium'))})",
                    "inline": True,
                }
            )
        line = yesterday_line(prev)
        if line:
            fields.append({"name": "Yesterday's calls, a day on", "value": line[:1000], "inline": False})
    else:
        s = summary(doc)
        fields = [
            {"name": "Most traded", "value": s["most"], "inline": True},
            {"name": "Largest trade", "value": s["largest"], "inline": True},
            {"name": "Bullish / bearish", "value": s["sides"], "inline": True},
            {"name": "In two or more tables", "value": s["names"][:1000], "inline": False},
        ]
    if with_calendar:
        fields.append(
            {
                "name": "Events",
                "value": f"```\n{calendar_text(doc['session'], calendar)[:990]}\n```",
                "inline": False,
            }
        )
    return {
        "title": _title_line(title, date.fromisoformat(doc["session"])),
        "color": EMBED_COLOUR,
        "fields": fields,
        "footer": {"text": _footer(doc)},
    }


def plan_messages(
    doc: dict, title: str, style: str, sections: list[str], calendar: dict | None, prev: dict | None = None
) -> list[dict]:
    """The messages a style sends, in order: {"payload": payload_json, "cards": [card titles to
    attach]}. Images are named here and captured by the caller."""
    # Events is a capture like any card (the post page's table, 2026-10-03); a calendar not captured
    # with the session has no card, so it is left out rather than failing the series.
    cards = [c for c in sections if c != EVENTS or (calendar and calendar.get("events"))]
    if style in ("cards", "singles"):
        per = CARDS_PER_MESSAGE if style == "cards" else 1
        # No header message (2026-10-03): the series' title line rides on the first picture, and each
        # picture goes out under its own text title, so the thread reads without opening one.
        groups = [cards[i : i + per] for i in range(0, len(cards), per)]
        out = [{"payload": {"content": " · ".join(f"**{c}**" for c in g)}, "cards": g} for g in groups]
        if out:
            # A Discord heading, so the series' title reads larger than each picture's own (2026-10-03).
            head = f"## {_title_line(title, date.fromisoformat(doc['session']))}"
            out[0]["payload"]["content"] = head + "\n\n" + out[0]["payload"]["content"]
        return out
    s = summary(doc)
    if style == "embed":
        fields = [
            {"name": "Most traded", "value": s["most"], "inline": True},
            {"name": "Largest trade", "value": s["largest"], "inline": True},
            {"name": "Bullish / bearish", "value": s["sides"], "inline": True},
        ]
        for name in sections:
            fields.append(
                {
                    "name": name,
                    "value": f"```\n{section_text(name, doc, calendar)[:990]}\n```",
                    "inline": False,
                }
            )
        embed = {
            "title": _title_line(title, date.fromisoformat(doc["session"])),
            "color": EMBED_COLOUR,
            "description": f"In two or more tables: {s['names']}",
            "fields": fields[:25],
            "footer": {"text": _footer(doc)},
        }
        return [{"payload": {"embeds": [embed]}, "cards": []}]
    # text: plain markdown, split at a section when it would pass the limit.
    head = (
        f"**{_title_line(title, date.fromisoformat(doc['session']))}**\n"
        f"Most traded {s['most']} · Largest {s['largest']}\n"
        f"Bullish / bearish {s['sides']}\n"
        f"In two or more tables: {s['names']}"
    )
    foot = f"-# {_footer(doc)}"
    blocks = [f"**{name}**\n```\n{section_text(name, doc, calendar)}\n```" for name in sections]
    messages, current = [], head
    for block in blocks:
        if len(current) + len(block) + 1 > TEXT_LIMIT:
            messages.append(current)
            current = block
        else:
            current += "\n" + block
    current = current + "\n" + foot if len(current) + len(foot) + 1 <= TEXT_LIMIT else current
    messages.append(current)
    if not messages[-1].endswith(foot):
        messages.append(foot)
    return [{"payload": {"content": m[:2000]}, "cards": []} for m in messages]


def morning_text(flow: dict, title: str, n: int = 8) -> str | None:
    """The morning after: how the last session's flows came out against the next morning's open
    interest — opened, closed or mixed — and the top flows' first score beside the confirmed one.
    None until the confirmation has run."""
    if not flow.get("confirmed_at"):
        return None
    every = (flow.get("flows") or []) + (flow.get("unread") or [])
    counts = {k: sum(1 for f in every if f.get("confirmed") == k) for k in ("opened", "closed", "mixed")}
    unchecked = sum(1 for f in every if not f.get("confirmed"))
    ranked = sorted(
        (f for f in flow.get("flows") or [] if f.get("confirmed_score") is not None),
        key=lambda f: -abs(f["confirmed_score"]),
    )
    rows = [
        [f"{f['score']:+.0f} → {f['confirmed_score']:+.0f}", f["symbol"], f.get("what") or "", f["confirmed"]]
        for f in ranked[:n]
    ]
    session = date.fromisoformat(flow["session"])
    head = f"**{title}, confirmed — {session:%a} {session.day} {session:%b}**"
    tally = (
        f"Opened {counts['opened']} · closed {counts['closed']} · mixed {counts['mixed']}"
        f" · not checked {unchecked}"
    )
    body = _table(rows, {0}) if rows else "(no flow could be checked)"
    return (
        f"{head}\n{tally}\n```\n{body}\n```\n-# From the change in each contract's open interest overnight."
    )


def weekly_text(days: list[dict], audit: dict | None, review: dict | None, title: str) -> str | None:
    """The Friday scorecard over the week's flow documents: what was read, what the checks found,
    how the confirmations came out, and how the calls did a day on — descriptive only; the verdict on
    the score is the fixed 40-session test. None for a week with no scored session."""
    if not days:
        return None
    flows = [f for d in days for f in d.get("flows") or []]
    unread = sum(len(d.get("unread") or []) for d in days)
    votes = {
        k: sum((d.get("checks") or {}).get("site_vote", {}).get(k, 0) for d in days)
        for k in ("agrees", "neutral", "opposite")
    }
    voted = sum(votes.values())
    deltas = [(d.get("checks") or {}).get("delta") or {} for d in days]
    broker = sum(x.get("broker", 0) for x in deltas)
    singles = sum(x.get("singles", 0) for x in deltas)
    off = sum(len(x.get("off") or []) for x in deltas)
    closes = [(d.get("checks") or {}).get("close") or {} for d in days]
    compared = sum(x.get("compared", 0) for x in closes)
    close_off = sum(len(x.get("off") or []) for x in closes)
    confirmed = [f for f in flows if f.get("confirmed")]
    opened = sum(1 for f in confirmed if f["confirmed"] == "opened")
    closed = sum(1 for f in confirmed if f["confirmed"] == "closed")
    hits = total = strong_hits = strong_total = 0
    for d in days:
        returns = ((d.get("outcomes") or {}).get("1d") or {}).get("returns") or {}
        for f in d.get("flows") or []:
            move, s = returns.get(f["symbol"]), _shown(f)
            if move in (None, 0) or s is None:
                continue
            hit = (move > 0) == (s > 0)
            total += 1
            hits += hit
            if abs(s) >= 30:
                strong_total += 1
                strong_hits += hit
    first = date.fromisoformat(min(d["session"] for d in days))
    lines = [
        f"**{title} scorecard — week of {first:%a} {first.day} {first:%b}**",
        f"Sessions {len(days)} · flows read {len(flows)} · unread {unread}",
        f"Read vs the reported sentiment: {votes['agrees']} of {voted} agree · {votes['opposite']} opposite"
        if voted
        else "Read vs the reported sentiment: —",
        f"Delta from the broker {broker} of {singles} · model off by >0.10 on {off}",
        f"Closes vs Dolt: {compared - close_off} of {compared} within 0.5%"
        if compared
        else "Closes vs Dolt: not checked yet",
        f"Confirmed {len(confirmed)}: opened {opened} · closed {closed}"
        f" · mixed {len(confirmed) - opened - closed}",
        f"A day on: strong calls {strong_hits} of {strong_total} their way · all read {hits} of {total}"
        if total
        else "A day on: no outcome yet",
    ]
    if audit and audit.get("checked"):
        lines.append(f"Hand checks vs Time & Sales: {audit['agree']} of {audit['checked']} agree")
    if review:
        lines.append(f"The fixed test: {review.get('sessions', 0)} of {review.get('needed', 40)} sessions")
    lines.append("-# Descriptive only: the verdict on the score is the fixed 40-session test.")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------
# The marker.
# ------------------------------------------------------------------------------------------------


def _marker_path() -> Path:
    return _home.state_dir() / "quikoptions-post.json"


def markers() -> dict:
    try:
        doc = json.loads(_marker_path().read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def mark_sent(session: str, index: int, how: dict, fresh: bool = False) -> None:
    """Record message `index` of `session` as landed, with the title, style and sections it went out
    under. `fresh` starts the day's record over (a forced re-post)."""
    all_ = markers()
    if fresh or session not in all_:
        all_[session] = {**how, "sent": []}
    entry = all_[session]
    if index not in entry["sent"]:
        entry["sent"] = sorted([*entry["sent"], index])
    entry["at"] = _now_et().isoformat(timespec="seconds")
    kept = dict(sorted(all_.items())[-MARKER_KEEP:])
    path = _marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(kept, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# ------------------------------------------------------------------------------------------------
# Capturing and posting.
# ------------------------------------------------------------------------------------------------


# Cards captured from the post page (`/post/flow`, outside the shell, without the columns a post does
# not need); every other card from the Options flow page itself.
POST_PAGE_CARDS = {"Derived flow", "Net by name", "Top spreads", "Events"}


def capture_card(session: str, name: str, out: Path) -> str | None:
    """Screenshot the Options flow card titled "<name> — <session>" into `out`; None on success."""
    node = shutil.which("node")
    if node is None:
        return "node is not on PATH"
    route = f"{'post/flow' if name in POST_PAGE_CARDS else 'flow'}?session={session}"
    argv = [
        node, str(UI_CHECK), "--route", route,
        "--card", f"{name} — {session}",
        "--shot", str(out), "--scale", "2", "--viewport", "1600x1200", "--timeout", "45000",
    ]  # fmt: skip
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        done = subprocess.run(
            argv, cwd=CONSOLE, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=150, creationflags=flags,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"ui-check did not finish: {exc}"
    if done.returncode != 0 or not out.exists():
        tail = " | ".join((done.stdout + done.stderr).strip().splitlines()[-3:])
        return f"ui-check exit {done.returncode}: {tail}"
    return None


def with_files(payload: dict, files: list[Path]) -> dict:
    """The payload as sent: mentions off always, and the attachments named when there are files."""
    out = {**payload, "allowed_mentions": NO_MENTIONS}
    if files:
        out["attachments"] = [{"id": i, "filename": p.name} for i, p in enumerate(files)]
    return out


def _multipart(payload: dict, files: list[Path]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n',
        json.dumps(payload).encode(),
    ]
    for i, path in enumerate(files):
        parts += [
            f"\r\n--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="files[{i}]"; filename="{path.name}"\r\n'.encode(),
            b"Content-Type: image/png\r\n\r\n",
            path.read_bytes(),
        ]
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def post(url: str, payload: dict, files: list[Path], opener=urllib.request.urlopen) -> str | None:
    """One message to the webhook; None on success, else why not. A 429 is waited out once, for the
    time Discord asks (at most 30 s), then retried; anything else is reported and left for the next
    run, which resumes from the marker."""
    body, ctype = _multipart(payload, files)
    for attempt in (1, 2):
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": ctype, "User-Agent": USER_AGENT}
        )
        try:
            with opener(req, timeout=30) as resp:
                return None if 200 <= resp.status < 300 else f"discord HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt == 1:
                try:
                    wait = float(json.loads(exc.read().decode() or "{}").get("retry_after", 5))
                except (ValueError, AttributeError):
                    wait = 5.0
                time.sleep(min(max(wait, 0.5), 30.0))
                continue
            return f"discord HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001 - a failed post is reported, and retried next run
            return f"discord post failed: {exc}"
    return "discord kept answering 429"


def webhook_url(choice: str) -> tuple[str | None, str | None]:
    """(url, why not) for the chosen webhook: `notify` (the suite's Discord notify webhook) or
    `dedicated` (the series' own). Exactly that one, or nothing: never the other as a fallback."""
    from cherrypick.notify import secrets
    from cherrypick.orchestrator import config as cfgmod

    entry = cfgmod.QUIKOPTIONS_WEBHOOKS.get(choice)
    if entry is None:
        return None, f"unknown webhook choice {choice!r} (notify or dedicated)"
    value = secrets.read_entry(entry)
    if value is secrets.KEYRING_UNAVAILABLE:
        return None, "the OS keyring is unavailable"
    if not value:
        return None, f"no {entry} webhook stored (cherrypick secrets-set --channel {entry})"
    return str(value), None


def run(
    session: str,
    *,
    dry_run: bool,
    force: bool,
    keep: Path | None,
    cfg: dict | None = None,
    webhook: str | None = None,
    overrides: dict | None = None,
) -> str:
    """'posted', 'skipped' (nothing to do, now or ever) or 'failed'. `webhook` and `overrides`
    (title, style, cards) replace the configured choices for this run, and are checked by the same
    rules."""
    from cherrypick.orchestrator import config as cfgmod

    if cfg is None:
        cfg = cfgmod.load_config()
    if overrides:
        cfg = {**cfg, "quikoptions": {**(cfg.get("quikoptions") or {}), **overrides}}
    settings = post_settings(cfg)
    why = settings["problem"]
    how = {"title": settings["title"], "style": settings["style"], "cards": settings["cards"]}
    if not settings["post"] and not dry_run:
        _log(f"{session}: quikoptions.post is off (it needs quikoptions.enabled too); nothing posted")
        return "skipped"
    doc = load_capture(session)
    if doc is None:
        _log(f"{session}: no capture that passed its checks; nothing posted")
        return "skipped"
    entry = markers().get(session) or {}
    sent = set(entry.get("sent") or []) if not force else set()
    if sent:
        # A series part-posted finishes as it started: the same title, style and sections.
        how = {k: entry.get(k, how[k]) for k in how}
    elif why is not None:
        _log(f"{session}: the series settings cannot be used ({why}); nothing posted")
        return "failed"
    doc = {**doc, "_flow": load_flow(session)}
    earlier = [d for d in _capture_sessions() if d < session]
    prev = load_flow(earlier[-1]) if earlier else None
    messages = plan_messages(
        doc, how["title"], how["style"], list(how["cards"]), load_calendar(session), prev
    )
    if len(sent) >= len(messages):
        _log(f"{session}: already posted")
        return "skipped"
    url, why_not = (None, None) if dry_run else webhook_url(webhook or settings["webhook"])
    if not dry_run and url is None:
        _log(f"{session}: {why_not}; nothing posted")
        return "failed"

    with tempfile.TemporaryDirectory() as tmp:
        folder = keep or Path(tmp)
        folder.mkdir(parents=True, exist_ok=True)
        files: list[list[Path]] = []
        for message in messages:
            shots = []
            for name in message["cards"]:
                slug = name.lower().replace(" / ", "-").replace(" ", "-")
                shot = folder / f"quikoptions-{session}-{slug}.png"
                why = capture_card(session, name, shot)
                if why is not None:
                    _log(f"{session}: capture of {name!r} failed: {why}; nothing posted")
                    return "failed"
                shots.append(shot)
            files.append(shots)
        if dry_run:
            _log(f"{session}: dry run ({how['style']}), {len(messages)} message(s), posted nothing")
            for m in messages:
                print(json.dumps(m["payload"], indent=1, ensure_ascii=False))
            return "skipped"
        first = True
        for index, message in enumerate(messages):
            if index in sent:
                continue
            why = post(url, with_files(message["payload"], files[index]), files[index])
            if why is not None:
                _log(f"{session}: message {index + 1} of {len(messages)}: {why}; the next run resumes here")
                return "failed"
            mark_sent(session, index, how, fresh=force and first)
            first = False
            if index < len(messages) - 1:
                time.sleep(PAUSE_S)
    _log(f"{session}: posted ({how['style']})")
    return "posted"


def _capture_sessions() -> list[str]:
    folder = capture_path("x").parent
    return sorted(p.stem for p in folder.glob("????-??-??.json")) if folder.exists() else []


def run_text(
    key: str, text: str | None, *, dry_run: bool, force: bool, cfg: dict, webhook: str | None, what: str
) -> str:
    """Post one plain message once under `key` (a morning follow-up or a weekly scorecard)."""
    settings = post_settings(cfg)
    if text is None:
        _log(f"{key}: nothing to post yet ({what})")
        return "skipped"
    if not dry_run and not settings["post"]:
        _log(f"{key}: quikoptions.post is off; nothing posted")
        return "skipped"
    if not force and (markers().get(key) or {}).get("sent"):
        _log(f"{key}: already posted")
        return "skipped"
    if dry_run:
        print(text)
        return "skipped"
    url, why_not = webhook_url(webhook or settings["webhook"])
    if url is None:
        _log(f"{key}: {why_not}; nothing posted")
        return "failed"
    why = post(url, with_files({"content": text[:2000]}, []), [])
    if why is not None:
        _log(f"{key}: {why}")
        return "failed"
    mark_sent(key, 0, {"title": settings["title"], "style": "text", "cards": [what]}, fresh=True)
    _log(f"{key}: posted")
    return "posted"


def run_morning(session: str | None, **kw) -> str:
    """The morning follow-up for the last scored session (or `session`), once its open interest has
    been checked."""
    days = [d for d in _capture_sessions() if session is None or d == session]
    flow = load_flow(days[-1]) if days else None
    cfg = kw.pop("cfg")
    if flow is None:
        _log("morning: no scored session")
        return "skipped"
    return run_text(
        f"{flow['session']}:morning",
        morning_text(flow, post_settings(cfg)["title"]),
        cfg=cfg,
        what="morning",
        **kw,
    )


def run_weekly(session: str | None, **kw) -> str:
    """The scorecard for the week holding `session` (default: the last captured session)."""
    from cherrypick.core import home

    sessions = _capture_sessions()
    anchor = (
        date.fromisoformat(session) if session else (date.fromisoformat(sessions[-1]) if sessions else None)
    )
    cfg = kw.pop("cfg")
    if anchor is None:
        _log("weekly: no captured session")
        return "skipped"
    year, week, _ = anchor.isocalendar()
    days = [
        f
        for d in sessions
        if date.fromisoformat(d).isocalendar()[:2] == (year, week)
        for f in [load_flow(d)]
        if f
    ]
    folder = home.data_dir("quikoptions")

    def read(name: str) -> dict | None:
        try:
            return json.loads((folder / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    text = weekly_text(days, read("audit-summary.json"), read("review.json"), post_settings(cfg)["title"])
    return run_text(f"{year}-W{week:02d}:weekly", text, cfg=cfg, what="weekly", **kw)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--session", default=None, help="YYYY-MM-DD; default today (ET)")
    ap.add_argument("--dry-run", action="store_true", help="capture and print the messages, post nothing")
    ap.add_argument("--force", action="store_true", help="post the whole series again for this session")
    ap.add_argument("--keep", type=Path, default=None, help="keep the card images in this directory")
    ap.add_argument("--webhook", choices=["notify", "dedicated"], default=None, help="this run only")
    ap.add_argument(
        "--style", choices=["cards", "singles", "embed", "text"], default=None, help="this run only"
    )
    ap.add_argument(
        "--cards", default=None, help='this run only: sections, comma-separated ("Top sweeps,Events")'
    )
    ap.add_argument("--title", default=None, help="this run only: the series title")
    ap.add_argument(
        "--kind",
        choices=["daily", "morning", "weekly"],
        default="daily",
        help="daily (after the close), morning (the confirmations) or weekly (Friday's scorecard)",
    )
    args = ap.parse_args(argv)
    if args.kind in ("morning", "weekly"):
        from cherrypick.orchestrator import config as cfgmod

        cfg = cfgmod.load_config()
        if args.title is not None:
            cfg = {**cfg, "quikoptions": {**(cfg.get("quikoptions") or {}), "post_title": args.title}}
        runner = run_morning if args.kind == "morning" else run_weekly
        outcome = runner(args.session, dry_run=args.dry_run, force=args.force, cfg=cfg, webhook=args.webhook)
        return 1 if outcome == "failed" else 0
    session = args.session or _now_et().date().isoformat()
    overrides: dict = {}
    if args.style:
        overrides["post_style"] = args.style
    if args.cards:
        overrides["post_cards"] = [c.strip() for c in args.cards.split(",") if c.strip()]
    if args.title is not None:
        overrides["post_title"] = args.title
    outcome = run(
        session,
        dry_run=args.dry_run,
        force=args.force,
        keep=args.keep,
        webhook=args.webhook,
        overrides=overrides,
    )
    return 1 if outcome == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
