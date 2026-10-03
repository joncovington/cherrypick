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
Net by name, Birdseye, Names across tables, Largest by contracts, Top sweeps, Top spreads, Vol /
OI) plus `Events`: the session's
high-impact releases with actual against estimate, and the next ones, from the calendar capture. In
the image styles Events is a header field (it has no card). Default: Derived flow (the top scored
flows and the net by name, from `scripts/quikoptions_flow.py`), Largest by contracts and Top sweeps,
as text (chosen 2026-10-03; the others stay configurable). The day does not need every table told.

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
    if name == "Birdseye":
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
    return f"{title} — {session:%a} {session.day} {session:%b %Y} (stocks)"


def _footer(doc: dict) -> str:
    captured = _captured(doc)
    # No source attribution (decided 2026-10-03): the footer is the capture time alone.
    return f"Captured {captured}." if captured else "Capture time not recorded."


# ------------------------------------------------------------------------------------------------
# The message plan: what each style sends. Pure.
# ------------------------------------------------------------------------------------------------


def header_embed(doc: dict, title: str, calendar: dict | None = None, with_calendar: bool = False) -> dict:
    """The image styles' first message: the title, the date and the day in four fields; Events as
    a fifth when it is a chosen section. The date is always in the title line and the capture time
    in the footer, whatever the title, so no title can drop them."""
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
    doc: dict, title: str, style: str, sections: list[str], calendar: dict | None
) -> list[dict]:
    """The messages a style sends, in order: {"payload": payload_json, "cards": [card titles to
    attach]}. Images are named here and captured by the caller."""
    cards = [c for c in sections if c != EVENTS]
    with_cal = EVENTS in sections
    if style in ("cards", "singles"):
        per = CARDS_PER_MESSAGE if style == "cards" else 1
        out = [{"payload": {"embeds": [header_embed(doc, title, calendar, with_cal)]}, "cards": []}]
        out += [{"payload": {"content": ""}, "cards": cards[i : i + per]} for i in range(0, len(cards), per)]
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


def capture_card(session: str, name: str, out: Path) -> str | None:
    """Screenshot the Options flow card titled "<name> — <session>" into `out`; None on success."""
    node = shutil.which("node")
    if node is None:
        return "node is not on PATH"
    argv = [
        node, str(UI_CHECK), "--route", f"flow?session={session}", "--card", f"{name} — {session}",
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
    messages = plan_messages(doc, how["title"], how["style"], list(how["cards"]), load_calendar(session))
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
    args = ap.parse_args(argv)
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
