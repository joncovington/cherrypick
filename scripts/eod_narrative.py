#!/usr/bin/env python3
"""Write the day's narrative beside the fact set — the one place AI touches this suite.

Deliberately NOT a package. `packages/*` are what the trading loops import; this is a script the
scheduler runs, so no module can import it, no package acquires an API key or a network dependency,
and deleting this file costs a note and nothing else. That distinction is the whole reason the old
`orchestrator/eod_insight.py` was retired rather than moved: it lived in the package whose watchdog
fired it, which put an AI call one refactor away from the reliability path.

Four constraints, each of which the retired version got wrong in at least one way:

**Facts in, prose out.** The agent is given the fact set JSON and nothing else — no database, no
ledger, no shell. Every claim it can make is therefore traceable to a recorded number. The old
version fed it six markdown reports, which is how it came to depend on them as an input corpus.

**Final sessions only.** A session is provisional until the overnight module settles the next
morning. Writing a narrative against numbers that will still move produces a record of something
that never happened.

**Written once, then frozen.** The note is the record of what was concluded that day, under the
rules and config of that day. Re-running silently restates history, so an existing note is left
alone unless `--force` is passed, and `--force` stamps a new version rather than pretending.

**It can only ever fail to write a note.** No exit path here touches the fact set, a ledger, or a
loop. If `claude` is missing, times out, or returns nothing, the day simply has facts and no prose.

Usage:
    python scripts/eod_narrative.py [--session YYYY-MM-DD] [--force] [--file-issues] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

# Scheduled runs happen under pythonw — no console. A CONSOLE child (claude, gh) spawned from a
# windowless parent gets a brand-new console window, which flashes over whatever the user is doing;
# on 2026-08-21 that was three windows popping over a live trading platform mid-session. Same
# constant and reason as scripts/advisor_checkpoint.py.
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


# Deliberately not imported from cherrypick.review: this script must run even if the package is not
# installed, and the artifact path is a published contract rather than an implementation detail.
STORE = Path(
    os.environ.get("REVIEW_DATA_DIR")
    or Path(os.environ.get("CHERRYPICK_HOME") or (Path.home() / ".cherrypick")) / "data" / "review"
)

# The agent gets no tools that can act. It reads what is on stdin and writes what it returns; the
# script -- never the agent -- puts anything on disk.
DISALLOWED = ["Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Task"]
TIMEOUT_SECONDS = 600
TREND_SESSIONS = 5
MAX_ISSUES_PER_RUN = 3
ISSUE_LABEL = "eod-finding"

PROMPT = """You are writing the end-of-day note for a personal options-trading suite.

You are given one or more daily FACT SETS as JSON. The most recent is today's; any others are prior
sessions, oldest first, for trend. These are the only facts you have. You have no database access,
no tools, and no way to check anything else — so every statement you make must be supported by a
number in this JSON, and where the JSON says null you must say the thing is not measured rather
than guessing or omitting it.

Things about this data that will mislead you if you do not know them:

- `null` means NOT RECORDED. It never means zero. Do not average it as zero, and do not report a
  null as an absence of cost or risk.
- `sample.effective_n` is the number of independent market events; `sample.n` is the row count.
  Trades sharing a symbol and session share one event. A module with 673 trades and effective_n 1
  observed ONE day. Reason about effective_n, and say so plainly when it is small.
- `sample.breaks` are dates whose either side must never be pooled. `null` there means the module
  does not track breaks at all, which is weaker than an empty list, not stronger.
- `sample.suspected_break` is a regime change nobody journaled. Treat it as a reason to distrust a
  comparison spanning it.
- `by_profile` is the ARM SPLIT, and for most modules it is the actual experiment: the arms run
  against the same underlying on the same sessions, so comparing them is worth far more than the
  raw sample suggests. A module-level total averages them and hides the finding.
- `expected_vs_observed` is each module's OWN model. The three modules' bases are not comparable
  with each other.
- `status: provisional` means the overnight module has not settled.

Write, in this order and in plain prose, no more than roughly 500 words:

1. **What happened** — the day, per module, leading with anything that needs attention.
2. **Expected against observed** — where a module had a model, whether the day matched it.
3. **What the arms say** — the comparison, with its sample honestly characterised.
4. **Trend** — only across sessions the breaks allow, and say when a window was too short to read.

Then, under a heading `## Recommendations`, zero to three concrete changes, each as a single
bullet starting with a bolded short title. A recommendation must name the module, what to change,
and the specific number that argues for it. If the day does not support any, write "None — the
sample does not support a change yet." and stop. Never recommend a change on a single session's
evidence without saying that is what it rests on.

Be direct and unhedged where the numbers are clear, and explicitly uncertain where they are thin.
Do not flatter the results. A losing day described plainly is more useful than one softened.
"""


def _load(session: str) -> dict | None:
    try:
        return json.loads((STORE / f"eod-{session}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _trend_context(session: str, count: int = TREND_SESSIONS) -> list[dict]:
    """Prior fact sets, oldest first. Best-effort: a missing day is simply not context."""
    out = []
    try:
        day = date.fromisoformat(session)
    except ValueError:
        return out
    for back in range(count, 0, -1):
        prior = _load((day - timedelta(days=back)).isoformat())
        if prior:
            out.append(prior)
    return out


def _run_claude(payload: str) -> tuple[str | None, str | None]:
    exe = shutil.which("claude")
    if not exe:
        return None, "claude not on PATH"
    try:
        proc = subprocess.run(
            [exe, "-p", PROMPT, "--disallowed-tools", *DISALLOWED],
            input=payload,
            capture_output=True,
            text=True,
            # UTF-8 explicitly. `text=True` alone decodes with the LOCALE encoding, which is cp1252
            # on Windows -- so every em dash the model emits came back as mojibake and was then
            # written out faithfully as UTF-8, baking the corruption into the note.
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS, creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return None, f"claude timed out after {TIMEOUT_SECONDS}s"
    except Exception as exc:  # noqa: BLE001 -- a note is never worth raising over
        return None, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return None, (proc.stderr or "")[:500] or f"claude exited {proc.returncode}"
    text = (proc.stdout or "").strip()
    return (text, None) if text else (None, "claude returned nothing")


def _recommendations(note: str) -> list[str]:
    """The bullets under the Recommendations heading, if any."""
    if "## Recommendations" not in note:
        return []
    tail = note.split("## Recommendations", 1)[1]
    out = []
    for line in tail.split("\n"):
        line = line.strip()
        if line.startswith("- ") and "None —" not in line and "None -" not in line:
            out.append(line[2:].strip())
        elif line.startswith("## "):
            break
    return out


def _file_issues(recs: list[str], session: str, dry_run: bool) -> list[dict]:
    """File each recommendation as a tracked item, deduped by title against open issues.

    Capped per run and deduped on purpose: an unattended agent that files an issue a day for the
    same standing observation produces a backlog nobody reads, which is worse than no tracking.
    """
    gh = shutil.which("gh")
    if not gh:
        return [{"ok": False, "reason": "gh not on PATH"}]
    try:
        existing = subprocess.run(
            [gh, "issue", "list", "--label", ISSUE_LABEL, "--state", "open",
             "--json", "title", "--limit", "100"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, creationflags=CREATE_NO_WINDOW,
        )
        titles = {i["title"] for i in json.loads(existing.stdout or "[]")}
    except Exception:  # noqa: BLE001
        titles = set()

    results = []
    for rec in recs[:MAX_ISSUES_PER_RUN]:
        title = rec.split("**")[1] if "**" in rec else rec[:60]
        title = f"EOD {session}: {title}".strip()
        if any(t.endswith(title.split(": ", 1)[-1]) for t in titles):
            results.append({"ok": True, "skipped": "already open", "title": title})
            continue
        if dry_run:
            results.append({"ok": True, "dry_run": True, "title": title})
            continue
        try:
            body = f"{rec}\n\n---\nFrom the end-of-day review for {session}. Facts: `eod-{session}.json`."
            proc = subprocess.run(
                [gh, "issue", "create", "--title", title, "--body", body, "--label", ISSUE_LABEL],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, creationflags=CREATE_NO_WINDOW,
            )
            results.append({"ok": proc.returncode == 0, "title": title,
                            "url": (proc.stdout or "").strip() or None,
                            "error": None if proc.returncode == 0 else (proc.stderr or "")[:200]})
        except Exception as exc:  # noqa: BLE001
            results.append({"ok": False, "title": title, "error": f"{type(exc).__name__}: {exc}"})
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", default=None, help="YYYY-MM-DD; default the most recent final set")
    ap.add_argument("--force", action="store_true", help="rewrite an existing note (stamps a new version)")
    ap.add_argument("--file-issues", action="store_true", help="file recommendations as tracked issues")
    ap.add_argument("--dry-run", action="store_true", help="print the note; write nothing, file nothing")
    args = ap.parse_args()

    session = args.session
    if not session:
        candidates = sorted(p.stem.removeprefix("eod-") for p in STORE.glob("eod-*.json"))
        session = candidates[-1] if candidates else None
    if not session:
        print(json.dumps({"ok": False, "reason": "no fact sets found"}))
        return 0

    facts = _load(session)
    if not facts:
        print(json.dumps({"ok": False, "session": session, "reason": "no fact set"}))
        return 0
    if facts.get("status") != "final":
        # Not an error: the provisional pass runs hours before the session is closed out.
        print(json.dumps({"ok": True, "session": session, "skipped": "session is provisional"}))
        return 0

    note_path = STORE / f"eod-{session}.note.md"
    if note_path.exists() and not args.force and not args.dry_run:
        print(json.dumps({"ok": True, "session": session, "skipped": "note already written (frozen)"}))
        return 0

    payload = json.dumps({"today": facts, "prior_sessions": _trend_context(session)}, indent=2)
    note, error = _run_claude(payload)
    if note is None:
        print(json.dumps({"ok": False, "session": session, "error": error}))
        return 0  # a missing note is never a failure worth a non-zero exit

    header = (
        f"# Note — {session}\n\n"
        f"_Written from `eod-{session}.json` (fact set v{facts.get('fact_version')}), "
        f"which is the only input. Interpretation, not measurement: every number it cites lives in "
        f"that artifact, and where the two disagree the artifact is right._\n\n---\n\n"
    )
    if args.dry_run:
        print(header + note)
        return 0

    note_path.write_text(header + note + "\n", encoding="utf-8")
    result = {"ok": True, "session": session, "note": str(note_path)}
    recs = _recommendations(note)
    result["recommendations"] = len(recs)
    if recs and args.file_issues:
        result["issues"] = _file_issues(recs, session, args.dry_run)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
