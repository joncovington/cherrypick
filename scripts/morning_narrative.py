#!/usr/bin/env python3
"""Write the morning narrative beside the overview fact pack — outside every package, by design.

Same fence as `eod_narrative.py`, for the same reasons: `packages/*` is what the trading loops
import, so a script the scheduler runs cannot be imported by a loop, no package acquires an API key
or a network dependency, and deleting this file costs a note and nothing else.

One deliberate deviation from the EOD fence: **WebSearch and WebFetch stay allowed.** The calendar
no longer needs them -- since fact version 4 the pack carries the week's releases and earnings with
their implied moves -- but the prose over the movers and the risk monitor does: the headline file
gives titles, not reasons. The fence still holds where it matters — the agent gets no Bash, no
Edit, no Write, so it can read the web but can only ever *return prose*; the script, never the
agent, puts anything on disk. Numbers about the market itself must still come from the inputs.

Three inputs, all read-only, all written by deterministic jobs before this runs: the fact pack
(`packages/overview`), the technicals report for the last session before it (`packages/technicals`:
movers, breadth, stages, rotation, leaders -- the same pairing rule the console uses), and the
morning's headlines (`scripts/fetch_headlines.py`: title, link, source, time). A missing input is
passed as null and the prompt says so; it never stops the note.

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
_HOME = Path(os.environ.get("CHERRYPICK_HOME") or (Path.home() / ".cherrypick"))
STORE = Path(os.environ.get("OVERVIEW_DATA_DIR") or _HOME / "data" / "overview")
TECHNICALS = _HOME / "data" / "technicals"
HEADLINES = _HOME / "data" / "market-report" / "headlines"
MAX_HEADLINES = 60

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
- Crude and gold as futures are `premarket.futures.cl` (/CL) and `premarket.futures.gc` (/GC):
  real contract prices, against their prior settle. `wti_proxy` and `gold_proxy` are ETF proxies
  (USO, GLD), not futures prices — quote the futures, and fall back to "the crude proxy" only when
  the future is unmeasured, never as a WTI or gold dollar price.

You are also given, when they exist:

- `technicals`: the market report for the last completed session -- `movers` (the largest
  single-stock moves, with volume against each name's own 50-session average), `breadth` (net
  leaders minus laggards against the S&P 500, ten sessions), `stages` by sector, `rotation` states
  and the relative-strength `leaders`. Computed, not opinions; cite them as the suite's readings.
- `headlines`: titles, sources and times from a handful of news feeds over the last day and a half.
  Titles only -- never quote a headline as a fact about a number the pack or report contains.

Either may be null (not fetched, or not yet written); say so rather than working around it.

Write, in this order, in plain prose, no more than roughly 700 words:

1. A single-sentence bolded headline for the morning.
2. **Stance** — one short paragraph: the phase, what is driving it, what would change it.
3. **Prior session** — what the tape did (the pack's prior readings and sector board, the
   technicals breadth), then **movers**: yields (the pack's `yields`), oil (its futures and proxy),
   and the technicals `movers` -- for each mover worth naming, the number from the report and, where
   a headline or a search explains it, why, labelled as the reason reported rather than as fact.
   A mover you cannot explain is named as unexplained, not given a guessed cause.
4. **Risk monitor** — the editorial section: whatever macro theme is currently live, clearly
   labeled as interpretation. This section never feeds the phase.
5. **Week ahead** — from the pack's `calendar`: the releases (times ET where the pack has them;
   say "time not published" where it has none), the earnings with their implied moves as the pack
   states them, the next FOMC. Do not look the calendar up on the web.
6. **Session drivers** — three or four bullets: Bullish / Bearish / Watch / Risk, each tied to a
   pack or report number or a calendar item.
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


def _technicals(session: str) -> dict | None:
    """The last technicals report dated BEFORE the pack's session -- the close the morning saw.
    Trimmed to what the note uses: the full stage lists would only be noise in the prompt."""
    days = sorted(
        p.stem.removeprefix("report-")
        for p in TECHNICALS.glob("report-????-??-??.json")
        if p.stem.removeprefix("report-") < session
    )
    if not days:
        return None
    try:
        doc = json.loads((TECHNICALS / f"report-{days[-1]}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not doc.get("ok"):
        return None
    return {
        "session": doc.get("session"),
        "movers": doc.get("movers"),
        "breadth": doc.get("breadth"),
        "stages": [
            {
                "sector": s.get("sector"),
                "net": s.get("net"),
                # The full counts where the report gives them: its lists leave out illiquid names
                # (2026-10-07), its counts do not.
                "leaders": s.get("leaders_count", len(s.get("leaders") or [])),
                "laggards": s.get("laggards_count", len(s.get("laggards") or [])),
            }
            for s in doc.get("stages") or []
        ],
        "rotation": doc.get("rotation"),
        "leaders": doc.get("leaders"),
        "signal_counts": {k: len(v) for k, v in (doc.get("signals") or {}).items()},
    }


def _headlines(session: str) -> dict | None:
    """The morning's headline file: the session's own, else the day before's (an early run)."""
    try:
        day = date.fromisoformat(session)
    except ValueError:
        return None
    for d in (day, day - timedelta(days=1)):
        try:
            doc = json.loads((HEADLINES / f"{d.isoformat()}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items = [{k: i.get(k) for k in ("title", "source", "published")} for i in doc.get("items") or []]
        return {"generated_at": doc.get("generated_at"), "items": items[:MAX_HEADLINES]}
    return None


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
            timeout=TIMEOUT_SECONDS,
            creationflags=CREATE_NO_WINDOW,
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

    payload = json.dumps(
        {
            "today": facts,
            "prior_sessions": _trend_context(session),
            "technicals": _technicals(session),
            "headlines": _headlines(session),
        },
        indent=2,
    )
    note, error = _run_claude(payload)
    if note is None:
        print(json.dumps({"ok": False, "session": session, "error": error}))
        return 0  # a missing note is never a failure worth a non-zero exit

    header = (
        f"# Morning note — {session}\n\n"
        f"_Written from `morning-{session}.json` (fact pack v{facts.get('fact_version')}), the\n"
        f"technicals report for the session before it, and the morning's headlines. Market numbers\n"
        f"come from those artifacts and nowhere else; why a stock moved, and the risk monitor, are\n"
        f"the agent's reading of the headlines and its own research. Where a number here disagrees\n"
        f"with an artifact, the artifact is right._\n\n---\n\n"
    )
    if args.dry_run:
        # A redirected stdout on Windows is cp1252, and a note carries "−" and "±".
        sys.stdout.reconfigure(encoding="utf-8")
        print(header + note)
        return 0

    note_path.write_text(header + note + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "session": session, "note": str(note_path)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
