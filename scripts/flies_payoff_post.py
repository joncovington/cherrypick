"""Post the flies "payoff at expiry" chart to Discord once each book has settled.

After the bell, for each ledger named (paper, live): once the flies module itself says the session
is settled, capture the console's payoff card for that session and post it, captioned with each
arm's settled net and the settlement print, to the suite's Discord webhook. One post per ledger per
session; a marker in `state/flies-payoff-post.json` (written only here, only after a post landed)
keeps a scheduled re-run from posting twice.

"Settled" is the module's own rule, imported rather than restated: paper is
`paper_loop.session_already_settled`; live waits for `live_loop.session_officially_settled`, so a
provisional last-trade settlement is never posted as the day's result. Net is
`cherrypick.core.ledgers`' flies rule (gross_pnl - fees over settled rows), the one every other
surface reads.

A script, not a package: it drives a browser and posts to a webhook, so a failure costs a picture
and never a settlement. The capture is the console's own `tools/ui-check.mjs --card`, which refuses
rather than crops the wrong thing. The card's title carries the session date and the page opens on
the ledger's latest session, so a run for a day the page no longer shows fails loudly rather than
posting another day's chart under this one's caption.

    python scripts/flies_payoff_post.py [--mode live --mode paper] [--session YYYY-MM-DD]
                                        [--dry-run] [--force] [--keep DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import home as _home
from cherrypick.core import ledgers as _ledgers
from cherrypick.flies import db as _fliesdb
from cherrypick.flies.live_loop import session_officially_settled
from cherrypick.flies.paper_loop import session_already_settled

REPO = Path(__file__).resolve().parents[1]
CONSOLE = REPO / "packages" / "console"
UI_CHECK = CONSOLE / "tools" / "ui-check.mjs"
MODES = ("live", "paper")
MARKER_KEEP = 30  # sessions of posted-markers kept; older ones can never be re-run by the job
USER_AGENT = "cherrypick-notifier/1.0 (+https://github.com/cherrypick)"  # notifier.py's; see _post


def _now_et() -> datetime:
    return datetime.now(ZoneInfo("America/New_York"))


def _log(line: str) -> None:
    stamped = f"{_now_et().isoformat(timespec='seconds')} {line}"
    try:
        print(stamped)
    except (OSError, UnicodeError):
        pass  # a console that cannot encode the caption; the log file below still gets it
    try:
        path = _home.logs_dir("flies") / "payoff-post.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(stamped + "\n")
    except OSError:
        pass  # a log line is never worth a failed post


def ledger_path(mode: str) -> str:
    return _fliesdb.live_db_path() if mode == "live" else _fliesdb.default_db_path()


def is_settled(mode: str, conn, session: str) -> bool:
    if mode == "live":
        return session_officially_settled(conn, session)
    return session_already_settled(conn, session)


def _money(value: float) -> str:
    return f"{'+' if value >= 0 else '-'}${abs(value):,.2f}"


def caption(mode: str, conn, session: str) -> str:
    """The day in one message: each arm's settled net (every book the day opened, so an arm that
    traded nothing reads $0.00 rather than vanishing) and the print it settled on."""
    net: dict[str, float] = {}
    for row in _ledgers.READERS["fly_book"](conn, session, session):
        net[row["arm"]] = net.get(row["arm"], 0.0) + row["net_pnl"]
    books = conn.execute(
        "SELECT arm, symbol, settlement_price, settlement_source FROM fly_books WHERE trade_date = ?"
        " ORDER BY arm",
        (session,),
    ).fetchall()
    prints = sorted(
        {
            f"{b['symbol']} settled {b['settlement_price']:.2f}"
            + (f" ({b['settlement_source']})" if b["settlement_source"] else "")
            for b in books
            if b["settlement_price"] is not None
        }
    )
    arms = sorted({b["arm"] for b in books}, key=lambda a: (-net.get(a, 0.0), a))
    head = f"**Flies {'LIVE' if mode == 'live' else 'paper'} — payoff at expiry, {session}**"
    if prints:
        head += " · " + "; ".join(prints)
    lines = [f"`{arm}` net {_money(net.get(arm, 0.0))}" for arm in arms]
    return "\n".join([head, *lines])[:1900]  # Discord caps content at 2000


def _markers() -> dict:
    try:
        return json.loads((_home.state_dir() / "flies-payoff-post.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def already_posted(session: str, mode: str) -> bool:
    return mode in (_markers().get(session) or {})


def mark_posted(session: str, mode: str) -> None:
    markers = _markers()
    markers.setdefault(session, {})[mode] = _now_et().isoformat(timespec="seconds")
    kept = dict(sorted(markers.items())[-MARKER_KEEP:])
    path = _home.state_dir() / "flies-payoff-post.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(kept, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def capture(mode: str, session: str, out: Path) -> str | None:
    """Screenshot the payoff card for `session` into `out`; None on success, else why not."""
    node = shutil.which("node")
    if node is None:
        return "node is not on PATH"
    argv = [
        node,
        str(UI_CHECK),
        "--route",
        f"flies/session?mode={mode}",
        "--card",
        f"payoff at expiry — {session}",
        "--shot",
        str(out),
        "--scale",
        "2",
        "--viewport",
        "1600x1100",
        "--timeout",
        "45000",
    ]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # pythonw would pop a terminal
    try:
        done = subprocess.run(
            argv, cwd=CONSOLE, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=150, creationflags=flags,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"ui-check did not finish: {exc}"
    # ui-check exits 0 on a skip (no browser, no puppeteer) and writes nothing, so the file is the
    # only proof a picture exists.
    if done.returncode != 0 or not out.exists():
        tail = " | ".join((done.stdout + done.stderr).strip().splitlines()[-3:])
        return f"ui-check exit {done.returncode}: {tail}"
    return None


def post(image: Path, text: str) -> str | None:
    """Upload one image with a caption to the Discord webhook; None on success, else why not."""
    from cherrypick.notify import secrets  # the orchestrator's keyring entry; imported late for tests

    url = secrets.get_webhook("discord")
    if not url:
        return "discord webhook not set (cherrypick secrets-set --channel discord)"
    boundary = uuid.uuid4().hex
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n',
            json.dumps({"content": text}).encode(),
            f"\r\n--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="files[0]"; filename="{image.name}"\r\n'.encode(),
            b"Content-Type: image/png\r\n\r\n",
            image.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    # Discord's Cloudflare front rejects urllib's default User-Agent with a 403 (notifier.py).
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": USER_AGENT},
    )  # fmt: skip
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return None if 200 <= resp.status < 300 else f"discord HTTP {resp.status}"
    except Exception as exc:  # noqa: BLE001 - a failed post is reported, and retried next run
        return f"discord post failed: {exc}"


def run_mode(mode: str, session: str, *, dry_run: bool, force: bool, keep: Path | None) -> str:
    """One ledger's turn. Returns 'posted', 'skipped' (nothing to do, now or ever) or 'failed'."""
    path = ledger_path(mode)
    if not Path(path).exists():
        _log(f"{mode}: no ledger at {path}")
        return "skipped"
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if conn.execute("SELECT COUNT(*) FROM fly_books WHERE trade_date = ?", (session,)).fetchone()[0] == 0:
            _log(f"{mode}: no book for {session}")
            return "skipped"
        if not is_settled(mode, conn, session):
            _log(f"{mode}: {session} not settled yet" + (" on an official print" if mode == "live" else ""))
            return "skipped"
        if already_posted(session, mode) and not force:
            _log(f"{mode}: {session} already posted")
            return "skipped"
        text = caption(mode, conn, session)
    finally:
        conn.close()

    with tempfile.TemporaryDirectory() as tmp:
        shot = (keep or Path(tmp)) / f"flies-payoff-{mode}-{session}.png"
        shot.parent.mkdir(parents=True, exist_ok=True)
        why = capture(mode, session, shot)
        if why is not None:
            _log(f"{mode}: capture failed: {why}")
            return "failed"
        if dry_run:
            _log(f"{mode}: dry run, captured {shot}, not posted:\n{text}")
            return "skipped"
        why = post(shot, text)
    if why is not None:
        _log(f"{mode}: {why}")
        return "failed"
    mark_posted(session, mode)
    _log(f"{mode}: posted {session}")
    return "posted"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mode", action="append", choices=MODES, help="ledger(s) to post; default both")
    ap.add_argument("--session", default=None, help="YYYY-MM-DD; default today (ET)")
    ap.add_argument("--dry-run", action="store_true", help="capture and caption, post nothing")
    ap.add_argument("--force", action="store_true", help="post even if this session was posted")
    ap.add_argument("--keep", type=Path, default=None, help="keep the screenshots in this directory")
    args = ap.parse_args(argv)
    session = args.session or _now_et().date().isoformat()
    outcomes = [
        run_mode(m, session, dry_run=args.dry_run, force=args.force, keep=args.keep)
        for m in (args.mode or MODES)
    ]
    return 1 if "failed" in outcomes else 0


if __name__ == "__main__":
    sys.exit(main())
