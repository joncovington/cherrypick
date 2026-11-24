"""The 2026-11-24 measurement boundary: a paper twin per live arm, and the unpinned `live-shadow` retires.

    python scripts/boundary_2026_11_24.py --arms pm-200 pm-1400            # dry run: the diff and the breaks
    python scripts/boundary_2026_11_24.py --arms pm-200 pm-1400 --apply    # back up, then write

Until now MEIC's paper measurement of live was one arm, `live-shadow`, marked `follows_live`: it resolves
to whatever `live.arm` names TODAY. That was fine while live ran one arm. Once `live.arm` can change
from one day to the next, the same name would carry two arms' paper results and the paper-live gap would
compare live against the wrong twin. A `shadow_of: "<arm>"` profile is pinned to one arm instead
(`paper._merged_params`, `paper.twin_for`), so each arm live may run has its own twin and a switch moves
none of them.

What this script does (each step idempotent; a step already applied reports "nothing to do"):
  1. meic.risk.json: adds `live-shadow-<arm>` (enabled, `shadow_of: <arm>`, no override) for each arm
     named. The arm must be in the registry and must not itself be a follower or a twin.
  2. meic.risk.json: retires `live-shadow` (enabled false, a dated `_retired` note; never deleted: its
     rows are the record of what live was measured against until now). `--keep-live-shadow` leaves it.
  3. paper_trades.db `measurement_breaks`, scope = the arm name, dated the day it is applied: `arm_added`
     for each twin (the paper loop journals the same row itself once the twin is enabled; the unique key
     makes either order a no-op) and `arm_retired` for live-shadow.

The break is declared at the change, not staged: live results are read per arm and symbol from here, so
the cut is only on the paper side, where `live-shadow` stops and the twins start. Run it from the live
checkout with the change that adds `shadow_of` merged (it refuses otherwise). Every in-session paper loop
reads its registry at session start, so run it between sessions. The registry is hand-formatted, so the
edit is a text patch that must parse to exactly the intended document, or the step refuses.

Everything it writes is copied first to ~/.cherrypick/backups/boundary-<date>-<stamp>/ (the ledger with
SQLite's backup API). Nothing here talks to a broker.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

SHADOW = "live-shadow"


def home() -> Path:
    return Path(os.environ.get("CHERRYPICK_HOME") or Path.home() / ".cherrypick")


def today_et() -> str:
    return dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def block_span(text: str, name: str) -> tuple[int, int]:
    """(start, end) of `"name": { ... }` inside the registry's `profiles`, end just past the closing brace.
    A string-aware brace scan, so a brace inside a `_note` does not end the block early."""
    key = f'"{name}"'
    at = text.find(key)
    if at < 0:
        raise ValueError(f"{name} is not in the registry")
    i = text.index("{", at)
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(text)):
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return at, j + 1
    raise ValueError(f"{name}: unbalanced braces")


def plan_registry(text: str, arms: list[str], retire: bool, date: str) -> tuple[str, list[str]]:
    """The patched registry text and a note per step; raises on anything the registry does not allow."""
    doc = json.loads(text)
    profiles = doc["profiles"]
    notes: list[str] = []
    eol = "\r\n" if "\r\n" in text else "\n"
    want = json.loads(text)
    new = text

    start, end = block_span(new, SHADOW)
    line_start = new.rfind("\n", 0, start) + 1
    indent = new[line_start:start]
    if indent.strip():
        indent = "    "
    inner = indent + "  "

    adds = []
    for arm in arms:
        twin = f"{SHADOW}-{arm}"
        found = profiles.get(arm)
        if not isinstance(found, dict) or arm.startswith("_"):
            raise ValueError(f"{arm!r} is not an arm in the registry")
        if found.get("follows_live") or found.get("shadow_of"):
            raise ValueError(f"{arm!r} is a follower or a twin: live cannot run it")
        if twin in profiles:
            if profiles[twin].get("shadow_of") == arm:
                notes.append(f"{twin}: nothing to do")
                continue
            raise ValueError(f"{twin} exists but is not a twin of {arm}")
        note = (
            f"From {date} (boundary_2026_11_24): {arm} as live would run it, plus the live block's "
            f"own overrides, pinned to {arm} so it does not move when live.arm does. "
            "Live's paper twin for this arm."
        )
        want["profiles"][twin] = {"enabled": True, "shadow_of": arm, "_note": note}
        adds.append(
            f'{indent}"{twin}": {{{eol}{inner}"enabled": true,{eol}{inner}"shadow_of": {json.dumps(arm)},'
            f'{eol}{inner}"_note": {json.dumps(note)}{eol}{indent}}}'
        )
        notes.append(f"{twin}: added")

    if retire and profiles.get(SHADOW, {}).get("enabled", True) is not False:
        note = (
            f"Retired {date} (boundary_2026_11_24): it followed whatever live.arm named, so a switch of "
            "live's arm would have pooled two arms under this name. Each arm live may run has its own "
            "twin, live-shadow-<arm>. Kept for its rows."
        )
        want["profiles"][SHADOW]["enabled"] = False
        want["profiles"][SHADOW]["_retired"] = note
        block = new[start:end]
        if '"enabled": true' not in block:
            raise ValueError(f'{SHADOW} has no `"enabled": true` to turn off; edit it by hand')
        patched = block.replace('"enabled": true', '"enabled": false', 1)
        patched = patched[:-1].rstrip() + f',{eol}{inner}"_retired": {json.dumps(note)}{eol}{indent}}}'
        new = new[:start] + patched + new[end:]
        notes.append(f"{SHADOW}: retired")
    elif retire:
        notes.append(f"{SHADOW}: nothing to do")

    if adds:
        _, end = block_span(new, SHADOW)
        new = new[:end] + "," + eol + ("," + eol).join(adds) + new[end:]

    if json.loads(new) != want:
        raise ValueError("the patched registry does not parse to the intended document")
    return new, notes


def break_rows(arms: list[str], retire: bool, date: str) -> list[dict]:
    rows = [
        {
            "break_date": date,
            "scope": f"{SHADOW}-{arm}",
            "kind": "arm_added",
            "reason": f"{SHADOW}-{arm} enters the roster: {arm} as live would run it, pinned to the arm",
            "detail": "live's paper measurement is per arm from here (boundary_2026_11_24)",
        }
        for arm in arms
    ]
    if retire:
        rows.append(
            {
                "break_date": date,
                "scope": SHADOW,
                "kind": "arm_retired",
                "reason": (
                    f"{SHADOW} retires: it followed whatever live.arm named, so a switch would pool two arms"
                ),
                "detail": "replaced by one twin per arm (boundary_2026_11_24); its rows are kept",
            }
        )
    return rows


def backup(h: Path, date: str, files: list[Path], ledger: Path | None) -> Path:
    dest = h / "backups" / f"boundary-{date}-{time.strftime('%Y%m%d-%H%M%S')}"
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        (dest / f.name).write_bytes(f.read_bytes())
    if ledger is not None and ledger.exists():
        src = sqlite3.connect(str(ledger))
        out = sqlite3.connect(str(dest / f"{ledger.parent.name}-{ledger.name}"))
        try:
            src.backup(out)
        finally:
            out.close()
            src.close()
    return dest


def journal(ledger: Path, rows: list[dict]) -> int:
    """Insert the rows the ledger does not have (its unique key is date, scope, kind); returns how many."""
    conn = sqlite3.connect(str(ledger))
    try:
        n = 0
        for r in rows:
            stamp = dt.datetime.now().isoformat()
            cur = conn.execute(
                "INSERT OR IGNORE INTO measurement_breaks "
                "(break_date, scope, kind, reason, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (r["break_date"], r["scope"], r["kind"], r["reason"], r["detail"], stamp),
            )
            n += cur.rowcount
        conn.commit()
        return n
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--arms", nargs="+", required=True, metavar="ARM", help="the arms live may run, each gets a twin"
    )
    ap.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    ap.add_argument("--keep-live-shadow", action="store_true", help="leave the unpinned live-shadow enabled")
    ap.add_argument("--date", default=None, help="the break date (default: today, ET)")
    args = ap.parse_args(argv)

    try:
        from cherrypick.meic import paper
    except ImportError as exc:  # pragma: no cover - the live checkout always has it
        print(f"REFUSED: cannot import cherrypick.meic ({exc})")
        return 1
    if not hasattr(paper, "SHADOW_OF_KEY"):
        print("REFUSED: this checkout has no shadow_of support; merge and pull it first")
        return 1

    date = args.date or today_et()
    h = home()
    registry = h / "config" / "meic.risk.json"
    ledger = h / "data" / "meic" / "paper_trades.db"
    if not registry.exists():
        print(f"REFUSED: no registry at {registry}")
        return 1
    retire = not args.keep_live_shadow
    old = registry.read_bytes().decode("utf-8")
    try:
        new, notes = plan_registry(old, args.arms, retire, date)
    except (ValueError, KeyError) as exc:
        print(f"REFUSED: {exc}")
        return 1

    for line in notes:
        print("  " + line)
    if new != old:
        print(f"\n--- {registry}")
        diff = difflib.unified_diff(old.splitlines(), new.splitlines(), "before", "after", lineterm="", n=1)
        for line in diff:
            print(line[:260] + (" ..." if len(line) > 260 else ""))
    rows = break_rows(args.arms, retire, date)
    for r in rows:
        print(f"\n+++ break paper_trades.db: {r['break_date']} {r['scope']} {r['kind']}")
    if not ledger.exists():
        print(f"\nnote: no paper ledger at {ledger}; the breaks would be journaled by the loop where it can")
    if not args.apply:
        print("\ndry run: nothing written (add --apply)")
        return 0

    dest = backup(h, date, [registry], ledger if ledger.exists() else None)
    if new != old:
        registry.write_bytes(new.encode("utf-8"))
    n = journal(ledger, rows) if ledger.exists() else 0
    state = "written" if new != old else "unchanged"
    print(f"\napplied: registry {state}, {n} break row(s) journaled, backup {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
