"""External reports: scheduling a person's own script and posting what it wrote.

(docs/guide/external-reports.md)

The suite never ships the script and never fetches for it. Config lists entries (`external_reports`);
the supervisor derives one daily job per entry that runs `run.py external-run <id>`; that command
starts the person's script as a subprocess with the suite's Python, then judges the file it left at
`data/external/<id>.json` against the contract (`cherrypick.core.external_report`) and, when the entry
asks, posts a text card to Discord once per `as_of`.

A script must live under the suite home (`~/.cherrypick`, or `$CHERRYPICK_HOME`): the suite runs only
what the person put in their own home, never a path a stray config line names.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cherrypick.core import external_report as _report
from cherrypick.core import home as _home

JOB_PREFIX = "external-"
ID_PATTERN = re.compile(r"^[a-z0-9-]+$")
POST_CHANNELS = {"notify": "discord", "reporting": "discord_reporting"}
# The reporting channel's earlier spelling (renamed 2026-10-03) resolves for good.
POST_ALIASES = {"dedicated": "reporting"}
SCRIPT_TIMEOUT_SECONDS = 15 * 60
CATCHUP_MINUTES = 240

# Discord's embed limits (description 4096, whole embed 6000); the card stays well inside them.
DESCRIPTION_BUDGET = 3500
MAX_CARD_ROWS = 25
CELL_WIDTH = 24


def external_dir() -> Path:
    return _home.data_dir() / "external"


def report_path(report_id: str) -> Path:
    return external_dir() / f"{report_id}.json"


def posted_state_path() -> Path:
    return _home.state_dir() / "external_posts.json"


def script_problem(script: Any) -> tuple[Path | None, str | None]:
    """The script's resolved path, or why it is refused. A relative path is read from the home;
    anything that resolves outside the home (a `..` climb, another drive, a link out) is refused."""
    if not isinstance(script, str) or not script.strip():
        return None, "script is empty"
    base = _home.home().resolve()
    path = Path(script.strip()).expanduser()
    if not path.is_absolute():
        path = base / path
    resolved = path.resolve()
    if not resolved.is_relative_to(base):
        return None, f"script {script!r} is outside the suite home ({base}); keep your scripts under it"
    if resolved.suffix != ".py":
        return None, f"script {script!r} is not a .py file"
    return resolved, None


def entries(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """`external_reports` resolved. Each entry carries `problem` (None, or why no job can be derived
    from it) alongside id, script, at_et, trading_days_only, post (None | "reporting" | "notify")
    and enabled. A bad entry stays listed so status and doctor can say why, never silently dropped."""
    raw = cfg.get("external_reports") or []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return [_entry_stub(None, "external_reports must be a list")]
    for item in raw:
        if not isinstance(item, dict):
            out.append(_entry_stub(None, "an entry must be an object"))
            continue
        rid = item.get("id")
        problem: str | None = None
        post = item.get("post")
        post = POST_ALIASES.get(post, post)
        at = item.get("at_et")
        resolved: Path | None = None
        if not isinstance(rid, str) or not ID_PATTERN.match(rid):
            problem = f"id {rid!r} must be lowercase letters, digits and hyphens"
        elif rid in seen:
            problem = f"id {rid!r} is used twice"
        elif not (isinstance(at, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at)):
            problem = f"at_et {at!r} must be HH:MM"
        elif post is not None and post not in POST_CHANNELS:
            problem = f"post must be null, 'reporting' or 'notify', not {post!r}"
        else:
            resolved, problem = script_problem(item.get("script"))
        args = item.get("args", [])
        timeout = item.get("timeout_minutes", SCRIPT_TIMEOUT_SECONDS // 60)
        writes = bool(item.get("writes_report", True))
        if problem is None and not (isinstance(args, list) and all(isinstance(a, str) for a in args)):
            problem = "args must be a list of strings"
        elif problem is None and not (
            isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and 1 <= timeout <= 180
        ):
            problem = f"timeout_minutes {timeout!r} must be between 1 and 180"
        elif problem is None and post is not None and not writes:
            problem = "post needs a report: set writes_report true, or post null and let the script post"
        if isinstance(rid, str):
            seen.add(rid)
        out.append(
            {
                "id": rid,
                "script": str(resolved) if resolved else item.get("script"),
                "at_et": at,
                "trading_days_only": bool(item.get("trading_days_only", True)),
                "post": post,
                "enabled": bool(item.get("enabled", True)),
                "args": list(args) if isinstance(args, list) else [],
                "timeout_minutes": timeout if isinstance(timeout, (int, float)) else 15,
                "writes_report": writes,
                "problem": problem,
            }
        )
    return out


def _entry_stub(rid: Any, problem: str) -> dict[str, Any]:
    return {
        "id": rid,
        "script": None,
        "at_et": None,
        "trading_days_only": True,
        "post": None,
        "enabled": False,
        "args": [],
        "timeout_minutes": 15,
        "writes_report": True,
        "problem": problem,
    }


# ------------------------------------------------------------------------------ the Discord card
def _cell(value: Any) -> str:
    text = "" if value is None else (f"{value:g}" if isinstance(value, float) else str(value))
    text = text.replace("\n", " ").replace("`", "'")
    return text if len(text) <= CELL_WIDTH else text[: CELL_WIDTH - 1] + "…"


def card_payload(doc: dict[str, Any]) -> dict[str, Any]:
    """The Discord message for a valid report: title, source, as_of, summary and the first table cut
    to fit an embed, ending "…and N more rows" when rows were left out. Pure."""
    lines = [f"Source: {doc['source']}", f"As of: {doc['as_of']}"]
    if doc.get("summary"):
        lines.append(doc["summary"])
    head = "\n".join(lines)
    table = next((s["table"] for s in doc["sections"] if "table" in s), None)
    description = head
    if table is not None:
        heading = next(s["heading"] for s in doc["sections"] if "table" in s)
        body = [" | ".join(_cell(c) for c in table["columns"])]
        budget = DESCRIPTION_BUDGET - len(head) - len(heading) - 40  # fences, heading, the "more" line
        shown = 0
        for row in table["rows"][:MAX_CARD_ROWS]:
            line = " | ".join(_cell(c) for c in row)
            if len("\n".join(body)) + len(line) + 1 > budget:
                break
            body.append(line)
            shown += 1
        text = "\n".join(body)
        left = len(table["rows"]) - shown
        more = f"\n…and {left} more row{'' if left == 1 else 's'}" if left else ""
        description = f"{head}\n\n**{heading}**\n```\n{text}\n```{more}"
    return {"embeds": [{"title": doc["title"][:256], "description": description[:4000]}]}


def _posted() -> dict[str, str]:
    try:
        data = json.loads(posted_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _remember(report_id: str, as_of: str) -> None:
    state = _posted()
    state[report_id] = as_of
    path = posted_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def post_if_new(entry: dict[str, Any], sender=None, webhook=None) -> dict[str, Any]:
    """Post the entry's report once per `as_of`. Returns {"posted": bool, "why": str}. An invalid
    report posts nothing; a failed send is not remembered, so the next run tries again."""
    result = _report.check_file(report_path(entry["id"]))
    if not result.valid or result.doc is None:
        return {"posted": False, "why": "invalid report: " + "; ".join(result.problems)}
    if entry["post"] is None:
        return {"posted": False, "why": "post is off for this entry"}
    doc = result.doc
    if _posted().get(entry["id"]) == doc["as_of"]:
        return {"posted": False, "why": f"as_of {doc['as_of']} already posted"}
    channel = POST_CHANNELS[entry["post"]]
    if sender is None:
        from cherrypick.notify.notifier import send_webhook as sender
    if webhook is None:
        from cherrypick.notify import secrets as _secrets

        webhook = _secrets.get_webhook
    url = webhook(channel)
    if not url:
        return {"posted": False, "why": f"no {channel} webhook stored in the keyring"}
    sent = sender(
        url,
        card_payload(doc),
        channel=channel,
        source=f"{JOB_PREFIX}{entry['id']}",
        kind="report",
        inputs=[report_path(entry["id"])],
    )
    if not sent.get("ok"):
        return {"posted": False, "why": f"send failed: {sent.get('error')}"}
    _remember(entry["id"], doc["as_of"])
    return {"posted": True, "why": "posted"}


# ----------------------------------------------------------------------------------- the job body
def run(cfg: dict[str, Any], report_id: str | None, runner=subprocess.run) -> dict[str, Any]:
    """The supervisor's `external-<id>` job: run the script, judge the file, post when asked.
    `ok` is false (the job fails, and says why in its log) when the script fails or the report is
    invalid -- a stale-looking card in the console should always have a failed job behind it."""
    entry = next((e for e in entries(cfg) if e["id"] == report_id), None)
    if entry is None:
        return {"ok": False, "error": f"no external_reports entry with id {report_id!r}"}
    if entry["problem"]:
        return {"ok": False, "id": report_id, "error": entry["problem"]}
    started = datetime.now(timezone.utc)
    try:
        proc = runner(
            [sys.executable, entry["script"], *entry["args"]],
            capture_output=True,
            text=True,
            timeout=entry["timeout_minutes"] * 60,
            cwd=str(Path(entry["script"]).parent),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "id": report_id, "error": f"script did not finish: {type(exc).__name__}: {exc}"}
    tail = (proc.stdout or "")[-1000:] + (proc.stderr or "")[-1000:]
    if proc.returncode != 0:
        return {"ok": False, "id": report_id, "error": f"script exited {proc.returncode}", "output": tail}
    out: dict[str, Any] = {
        "id": report_id,
        "output": tail,
        "seconds": (datetime.now(timezone.utc) - started).seconds,
    }
    if not entry["writes_report"]:
        # A step that does its own work (a capture, a post of its own): the exit code is the verdict.
        out["ok"] = True
        return out
    checked = _report.check_file(report_path(report_id))
    if not checked.valid:
        out.update(ok=False, error="invalid report: " + "; ".join(checked.problems))
        return out
    out["ok"] = True
    out["as_of"] = checked.doc["as_of"] if checked.doc else None
    if entry["post"]:
        out["post"] = post_if_new(entry)
    return out
