"""Post a session's QuikOptions Hot Options Report to its own Discord channel, as a short series.

The series (docs/quikoptions-plan.md, decided 2026-10-03, shortened the same day): a header embed
(the configured title, the session, a few summary fields, and a footer naming QuikOptions as the
source with the capture time), then the Options flow cards named in `quikoptions.post_cards`, two to
a message so Discord shows them side by side. The default is the header and one message: Largest
outrights + Top sweeps. The day does not need every table to be told.

The images are the console's own Options flow cards, captured with `tools/ui-check.mjs --card`,
which refuses rather than crops the wrong thing: every card is titled "<name> — <session>", so a
page still showing another day fails the run instead of posting it under this one's header. All six
are captured before anything is posted, so a capture failure never leaves a header without its
pictures. The header's figures come from the saved capture, never from a picture.

A script, not a package: it drives a browser and pushes a webhook, so a failure costs a post and
never a capture. It posts only to its OWN webhook (`cherrypick secrets-set --channel
discord_quikoptions`, `post_webhook: "dedicated"`) or to the suite's Discord notify webhook
(`post_webhook: "notify"`): a choice, never a fallback. With the chosen one not stored it posts
nothing and says why. Every message goes out with Discord's mentions switched off, so a title or
caption can never ping anyone.

Once per session: `state/quikoptions-post.json` (written only here, only after a message landed)
records which messages of which session went out, and the title they went out under. A re-run sends
only what is missing, in order, under that same title, so a failure halfway resumes rather than
repeating the header, and one day never carries two titles. It posts only a capture that passed
the page's own checks; with no capture for the session it posts nothing.

    python scripts/quikoptions_post.py [--session YYYY-MM-DD] [--webhook notify|dedicated]
                                       [--dry-run] [--force] [--keep DIR]
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
from datetime import date, datetime
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


def message_cards(cards: list[str]) -> list[list[str]]:
    """The image messages in order: the chosen cards, two to a message (the last may hold one)."""
    return [cards[i : i + CARDS_PER_MESSAGE] for i in range(0, len(cards), CARDS_PER_MESSAGE)]


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
# The capture, the title, the header. Pure where it can be.
# ------------------------------------------------------------------------------------------------


def capture_path(session: str) -> Path:
    return _home.data_dir("quikoptions") / "hot-options" / f"{session}.json"


def load_capture(session: str) -> dict | None:
    """The saved capture for `session`, or None. A `.rejected` capture is a different file, so it is
    never read here: only a capture that passed the page's own checks is posted."""
    try:
        doc = json.loads(capture_path(session).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return (
        doc if isinstance(doc, dict) and doc.get("session") == session and not doc.get("problems") else None
    )


def post_settings(cfg: dict) -> dict:
    """{post, problem, title, webhook, cards}. The raw values are checked here, not the settings'
    fallbacks: a broken title, an unknown webhook or card refuses the run and says why, rather than
    posting something nobody chose."""
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
    }


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


TABLE_WORD = {"outrights": "outright", "sweeps": "sweep", "spreads": "spread"}


def header_embed(doc: dict, title: str) -> dict:
    """The series' first message: the title, the session, and the day in five fields, every figure
    from the saved capture. The source and capture time are always in the footer, whatever the
    title, so no title can drop the attribution or the date."""
    session = date.fromisoformat(doc["session"])
    tables = doc.get("tables") or {}
    derived = doc.get("derived") or {}
    top = (tables.get("birdseye") or [None])[0]
    most = f"{top['symbol']} · {(top.get('shown') or {}).get('total', '—')} trades" if top else "—"
    big = derived.get("largest_trade")
    largest = "—"
    if big:
        row = next(
            (r for r in tables.get(big["table"], []) if r.get("symbol") == big.get("symbol")),
            {},
        )
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
    saved = doc.get("saved_at")
    captured = ""
    if saved:
        try:
            captured = f" · captured {datetime.fromisoformat(saved).astimezone(ET):%H:%M} ET"
        except ValueError:
            captured = ""
    return {
        "title": f"{title} — {session:%a} {session.day} {session:%b %Y} (stocks)",
        "color": EMBED_COLOUR,
        "fields": [
            {"name": "Most traded", "value": most, "inline": True},
            {"name": "Largest trade", "value": largest, "inline": True},
            {"name": "Bullish / bearish", "value": f"{side('Bullish')} / {side('Bearish')}", "inline": True},
            {"name": "In two or more tables", "value": names[:1000], "inline": False},
        ],
        "footer": {
            "text": f"Source: QuikOptions Hot Options Report{captured}. The side is the site's own call."
        },
    }


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


def mark_sent(session: str, index: int, title: str, cards: list[str]) -> None:
    all_ = markers()
    entry = all_.setdefault(session, {"title": title, "cards": cards, "sent": []})
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


NO_MENTIONS = {"parse": []}


def message_payload(index: int, embed: dict, images: list[list[Path]]) -> tuple[dict, list[Path]]:
    """(payload_json, files) for message `index`: the header embed alone, then each message's cards."""
    if index == 0:
        return {"embeds": [embed], "allowed_mentions": NO_MENTIONS}, []
    files = images[index - 1]
    attachments = [{"id": i, "filename": p.name} for i, p in enumerate(files)]
    return {"content": "", "attachments": attachments, "allowed_mentions": NO_MENTIONS}, files


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
) -> str:
    """'posted', 'skipped' (nothing to do, now or ever) or 'failed'. `webhook` overrides the
    configured choice for this run (a test post to the notify channel, say)."""
    if cfg is None:
        from cherrypick.orchestrator import config as cfgmod

        cfg = cfgmod.load_config()
    settings = post_settings(cfg)
    why, title, cards = settings["problem"], settings["title"], settings["cards"]
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
        # A series part-posted finishes as it started: the same title and the same cards.
        title, cards = entry.get("title", title), list(entry.get("cards") or cards)
    elif why is not None:
        _log(f"{session}: the series settings cannot be used ({why}); nothing posted")
        return "failed"
    groups = message_cards(cards)
    total = 1 + len(groups)
    if len(sent) >= total:
        _log(f"{session}: already posted")
        return "skipped"
    url, why_not = (None, None) if dry_run else webhook_url(webhook or settings["webhook"])
    if not dry_run and url is None:
        _log(f"{session}: {why_not}; nothing posted")
        return "failed"

    embed = header_embed(doc, title)
    with tempfile.TemporaryDirectory() as tmp:
        folder = keep or Path(tmp)
        folder.mkdir(parents=True, exist_ok=True)
        images: list[list[Path]] = []
        for group in groups:
            shots = []
            for name in group:
                slug = name.lower().replace(" / ", "-").replace(" ", "-")
                shot = folder / f"quikoptions-{session}-{slug}.png"
                why = capture_card(session, name, shot)
                if why is not None:
                    _log(f"{session}: capture of {name!r} failed: {why}; nothing posted")
                    return "failed"
                shots.append(shot)
            images.append(shots)
        if dry_run:
            _log(f"{session}: dry run, captured {sum(map(len, images))} cards into {folder}, posted nothing")
            print(json.dumps(embed, indent=1, ensure_ascii=False))
            return "skipped"
        for index in range(total):
            if index in sent:
                continue
            payload, files = message_payload(index, embed, images)
            why = post(url, payload, files)
            if why is not None:
                _log(f"{session}: message {index + 1} of {total}: {why}; the next run resumes here")
                return "failed"
            mark_sent(session, index, title, cards)
            if index < total - 1:
                time.sleep(PAUSE_S)
    _log(f"{session}: posted")
    return "posted"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--session", default=None, help="YYYY-MM-DD; default today (ET)")
    ap.add_argument("--dry-run", action="store_true", help="capture and print the header, post nothing")
    ap.add_argument("--force", action="store_true", help="post the whole series again for this session")
    ap.add_argument("--keep", type=Path, default=None, help="keep the card images in this directory")
    ap.add_argument(
        "--webhook",
        choices=["notify", "dedicated"],
        default=None,
        help="this run only: the suite's Discord notify webhook, or the series' own (default: the config)",
    )
    args = ap.parse_args(argv)
    session = args.session or _now_et().date().isoformat()
    outcome = run(session, dry_run=args.dry_run, force=args.force, keep=args.keep, webhook=args.webhook)
    return 1 if outcome == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
