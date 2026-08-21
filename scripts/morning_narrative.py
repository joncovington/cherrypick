#!/usr/bin/env python3
"""Write the morning narrative beside the overview fact pack — outside every package, by design.

Same fence as `eod_narrative.py`, for the same reasons: `packages/*` is what the trading loops
import, so a script the scheduler runs cannot be imported by a loop, no package acquires an API key
or a network dependency, and deleting this file costs a note and nothing else.

One deliberate deviation from the EOD fence: **WebSearch and WebFetch stay allowed.** The morning
report's macro calendar (CPI/PPI/retail-sales times, notable earnings) has no deterministic source
in the suite, and the decision was to have the narrative agent look it up at render time rather
than maintain a curated file that goes stale. The fence still holds where it matters — the agent
gets no Bash, no Edit, no Write, so it can read the web but can only ever *return prose*; the
script, never the agent, puts anything on disk. Numbers about the market itself must still come
from the pack: the prompt is explicit that web results may inform the calendar and the editorial
risk monitor only.

The other constraints carry over unchanged. The pack is the only market input. The note is written
once and frozen unless `--force`. And every exit path can only ever fail to write a note — nothing
here touches the pack, a ledger, or a loop.

Usage:
    python scripts/morning_narrative.py [--session YYYY-MM-DD] [--force] [--dry-run]
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


# Deliberately not imported from cherrypick.overview: this script must run even if the package is
# not installed, and the artifact path is a published contract rather than an implementation detail.
STORE = Path(
    os.environ.get("OVERVIEW_DATA_DIR")
    or Path(os.environ.get("CHERRYPICK_HOME") or (Path.home() / ".cherrypick")) / "data" / "overview"
)

# No acting tools. WebSearch/WebFetch are deliberately absent from this list -- see the module
# docstring -- which is the one difference from eod_narrative.py's fence.
DISALLOWED = ["Bash", "Edit", "Write", "NotebookEdit", "Task"]
TIMEOUT_SECONDS = 600
TREND_SESSIONS = 5

PROMPT = """You are writing the pre-open morning note for a personal options-trading suite.

You are given one or more morning FACT PACKS as JSON. The most recent is today's; any others are
prior sessions, oldest first, for trend. Every market number you cite must come from these packs.
You may use web search for exactly two purposes: (1) today's and this week's macro calendar (data
releases, times ET) and notable earnings, and (2) context for the editorial risk-monitor section.
Never replace, adjust, or second-guess a number in the pack with one from the web — if the web
disagrees with the pack, the pack is what this suite measured, and you may at most note the
discrepancy.

Things about this data that will mislead you if you do not know them:

- `null` means NOT MEASURED. Never zero, never omitted silently: say the thing is not measured.
- Every reading carries `basis`: `live` is a fresh pre-open quote; `prior` is the last completed
  session's confirmed value, and its `session` field says which. Attribute prior values to their
  session — "Friday's close", not "this morning".
- `phase` and `gates` are MECHANICAL, computed from declared thresholds. Report the phase as
  computed. You may argue with it editorially, but clearly as opinion, and you must never restate
  the phase as something other than what the pack says.
- `levels` (gamma flip, call wall, put wall) come from this suite's own GEX engine. Pre-open they
  are the prior session's last confirmed recording — label them so.
- `wti_proxy` and `gold_proxy` are ETF proxies (USO, GLD), not futures prices. Say "the crude
  proxy", never a WTI dollar price the pack does not contain.

Write, in this order, in plain prose, no more than roughly 600 words:

1. A single-sentence bolded headline for the morning.
2. **Stance** — one short paragraph: the phase, what is driving it, what would change it.
3. **What happened / What's next** — the prior session from the pack's prior readings and sector
   board; the coming session from the calendar you looked up.
4. **Risk monitor** — the editorial section: whatever macro theme is currently live, clearly
   labeled as interpretation. This section never feeds the phase.
5. **Macro & earnings calendar** — today's releases with times ET, the week's headliners.
6. **Session drivers** — three or four bullets: Bullish / Bearish / Watch / Risk, each tied to a
   pack number or a calendar item.
7. A footer line: the date, the phase, and a WATCH: list of the levels and events named above.

Be direct where the numbers are clear, explicitly uncertain where they are thin, and honest about
how much of this morning's picture is prior-session data. Do not flatter the tape.
"""


def _load(session: str) -> dict | None:
    try:
        return json.loads((STORE / f"morning-{session}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _trend_context(session: str, count: int = TREND_SESSIONS) -> list[dict]:
    """Prior packs, oldest first. Best-effort: a missing day is simply not context."""
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
            # UTF-8 explicitly: text=True alone decodes with the locale encoding (cp1252 on
            # Windows), which bakes mojibake into the note. Same lesson as eod_narrative.py.
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", default=None, help="YYYY-MM-DD; default the most recent pack")
    ap.add_argument("--force", action="store_true", help="rewrite an existing note")
    ap.add_argument("--dry-run", action="store_true", help="print the note; write nothing")
    args = ap.parse_args()

    session = args.session
    if not session:
        candidates = sorted(p.stem.removeprefix("morning-") for p in STORE.glob("morning-*.json"))
        session = candidates[-1] if candidates else None
    if not session:
        print(json.dumps({"ok": False, "reason": "no fact packs found"}))
        return 0

    facts = _load(session)
    if not facts:
        print(json.dumps({"ok": False, "session": session, "reason": "no fact pack"}))
        return 0

    note_path = STORE / f"morning-{session}.note.md"
    if note_path.exists() and not args.force and not args.dry_run:
        print(json.dumps({"ok": True, "session": session, "skipped": "note already written (frozen)"}))
        return 0

    payload = json.dumps({"today": facts, "prior_sessions": _trend_context(session)}, indent=2)
    note, error = _run_claude(payload)
    if note is None:
        print(json.dumps({"ok": False, "session": session, "error": error}))
        return 0  # a missing note is never a failure worth a non-zero exit

    header = (
        f"# Morning note — {session}\n\n"
        f"_Written from `morning-{session}.json` (fact pack v{facts.get('fact_version')}). Market\n"
        f"numbers come from that artifact and nowhere else; the macro calendar and the risk-monitor\n"
        f"section are the agent's own render-time research. Where a market number here disagrees\n"
        f"with the artifact, the artifact is right._\n\n---\n\n"
    )
    if args.dry_run:
        print(header + note)
        return 0

    note_path.write_text(header + note + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "session": session, "note": str(note_path)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
