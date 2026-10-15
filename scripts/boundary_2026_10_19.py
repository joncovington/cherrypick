"""The 2026-10-15 measurement boundary: every config change, ledger break and experiment wind-down
the batch needs, in one dry-run-by-default pass.

    python scripts/boundary_2026_10_19.py                         # dry run: what would change, as diffs
    python scripts/boundary_2026_10_19.py --apply                 # back up, then write (from 2026-10-14)
    python scripts/boundary_2026_10_19.py --apply --enable-flies-mid-first   # also the live pricing switch

Moved from 2026-10-19 to 2026-10-15 on the evening of 2026-10-14, before any session ran under it (the
file keeps its name). Run it that evening, from the live checkout with main merged (it checks the code
it needs is there and refuses a step whose code is missing). Every in-session loop reads its config at
session start, so the next morning's first session runs the new roster; nothing needs a restart.

What takes effect by itself, with no help from this script, once the batch is merged:
  - earnings: atm_calendar and double_calendar open nothing in strat_test from 2026-10-15
    (strat_test_harness.STRAT_TEST_RETIRED); this script only journals the break.
  - flies: the live-shadow, selector, range-centre and window-1300 arms' arm_added breaks journal
    themselves once enabled.

What this script does (each step idempotent; a step already applied reports "nothing to do"):
  1. advisor: concludes the experiments being wound down with `python -m cherrypick.advisor kill`,
     which computes and stores each verdict and re-issues Monday's artifact without them. Done FIRST:
     a bound removed under a running experiment rejects its next artifact instead of concluding it.
       flies miss-stop-30 (and miss-stop-90 if still active), calendars fri-noon-cycle-extension,
       earnings dc-stop-at-75pct-debit, meic live-stop-vs-hold.
  2. flies.json: retires debit-first-up/-down, bwb-up/-down, bwb-up-w2/-down-w2 and bwb-atm (enabled
     false, a dated note; never deleted); adds the live-shadow arm; adds the range-centre and
     window-1300 arms (declared 2026-10-14), each a copy of this machine's control with its one
     variable on top; enables the selector (declared for this boundary 2026-10-04; --skip-selector
     leaves it); removes miss_stop_minutes from the advice bounds. With --enable-flies-mid-first
     only: live.entry_pricing "mid_first" and a LIVE ledger break.
  3. bwb.json: retires the delta and wall books (enabled false). Their open rows run off by expiry.
     Copies the add-on bounds' `arms` from packages/bwb/config.example.json (PR #224), so the
     advisor refuses a trigger param on a base that never arms; skipped with a note before #224.
  4. curve.json: retires the hook book.
  5. calendars.json: removes time_exit from the advice bounds.
  6. meic.risk.json: adds the four live-minus-one-gate arms.
  7. measurement_breaks, one row per change, dated 2026-10-15, in each ledger's own schema.

Everything it writes is copied first to ~/.cherrypick/backups/boundary-2026-10-15-<stamp>/; ledgers
are copied with SQLite's backup API (WAL-safe). Nothing here talks to a broker.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

BOUNDARY = "2026-10-15"
EARLIEST_APPLY = "2026-10-14"
JOURNAL = "docs/experiment-log.md and each module's journal, 2026-10-12"

FLIES_RETIRE = {
    "debit-first-up": "read at 15 sessions: -$1,155 behind control 09-21..10-12; the debit-first family "
    "loses to fees (gross -$34.77 against $1,979 of fees over 200 entries)",
    "debit-first-down": "read at 15 sessions: -$3,806 behind control 09-21..10-12; the debit-first family "
    "loses to fees (gross -$34.77 against $1,979 of fees over 200 entries)",
    "bwb-up": "4 fills in 16 sessions: the market does not offer the credit at the 0.75 floor",
    "bwb-down": "2 fills in 16 sessions: the market does not offer the credit at the 0.75 floor",
    "bwb-up-w2": "0 fills in 16 sessions: the 1.50 floor sits above every credit measured",
    "bwb-down-w2": "0 fills in 16 sessions: the 1.50 floor sits above every credit measured",
    "bwb-atm": "-$3,147 on 85 entries against control's +$1,018 on 123, 09-21..10-12; as traded the roll "
    "cost $756 against never rolling",
}
LIVE_SHADOW = {
    "enabled": True,
    "mirror_live": True,
    "_note": f"From {BOUNDARY} ({JOURNAL}): the LIVE configuration in paper. mirror_live takes every "
    "param from the live block (engine.live_mirror_params); any other key here is ignored. Read against "
    "the live ledger over the same sessions, never against control.",
}
# control plus one variable each (declared 2026-10-14, docs/experiment-log.md 2026-10-14). The step
# copies the machine's own control and lays these on top, so neither can differ from it in anything else.
FLIES_ADD = {
    "range-centre": (
        {"range_pos_max": 0.8, "center_above_spot_max": 1.25},
        "control plus a placement gate: no entry at or above 0.8 of the session range so far, or with the "
        "ATM centre more than 1.25 points above spot (engine.placement_refusal)",
    ),
    "window-1300": (
        {"entry_windows": [["13:00", "13:59"]]},
        "control with its entries confined to 13:00-13:59",
    ),
}
MEIC_MINUS = {
    "live-minus-ivrank": {"min_iv_rank": 0.0},
    "live-minus-gexblock": {"regime_gex_block_negative": False},
    "live-minus-otmfloor": {"min_call_otm_pct": 0.0001, "min_put_otm_pct": 0.0001},
    "live-minus-latebias": {"late_entry_bias_enabled": False},
}
_WOUND = "wound down at the 2026-10-15 boundary: "
KILLS = {
    "exp-2026-10-12-flies-1": _WOUND + "miss_stop_minutes leaves flies' advice bounds",
    "exp-2026-09-24-flies-1": _WOUND + "miss_stop_minutes leaves flies' advice bounds",
    "exp-2026-10-02-calendars-1": _WOUND
    + "time_exit leaves calendars' advice bounds (exit_policies replays Friday noon every run)",
    "exp-2026-10-07-earnings-1": _WOUND + "double_calendar retired from strat_test",
    "exp-2026-09-29-meic-2": _WOUND + "the replay found hold beats every stop at every cap; forward 26% won",
}


# --------------------------------------------------------------------------- plumbing
def home() -> Path:
    return Path(os.environ.get("CHERRYPICK_HOME") or Path.home() / ".cherrypick")


class Plan:
    """Collected changes: config texts (path -> (old, new)), break rows, kills, notes."""

    def __init__(self):
        self.files: dict[Path, tuple[str, str]] = {}
        self.breaks: list[tuple[Path, str, dict]] = []  # (db, schema, row)
        self.kills: list[tuple[str, str]] = []
        self.notes: list[str] = []
        self.refused: list[str] = []


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _dump(doc: dict) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=True) + "\n"


def _eol(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _round_trips(text: str) -> bool:
    return _dump(json.loads(text)).strip() == text.replace("\r\n", "\n").strip()


def _edit_json(path: Path, mutate, text_patch=None) -> tuple[str, str] | None:
    """Apply `mutate(doc)` to a config. A file the suite wrote as indent-2 JSON is re-dumped; a
    hand-formatted one is changed with `text_patch(text)` instead, and the patched text must parse to
    exactly the mutated document, or the step refuses. Returns (old, new) or None when unchanged."""
    old = _read(path)
    doc = json.loads(old)
    want = json.loads(old)
    mutate(want)
    if want == doc:
        return None
    if _round_trips(old):
        new = _dump(want).replace("\n", _eol(old))  # keep the file's own line endings
    else:
        if text_patch is None:
            raise RuntimeError(f"{path.name} is hand-formatted and this step has no text patch")
        new = text_patch(old)
        if json.loads(new) != want:
            raise RuntimeError(f"{path.name}: the text patch does not produce the intended document")
    return old, new


def _disable_block(text: str, block: str, note: str) -> str:
    """`"<block>": { ... "enabled": true` -> `"enabled": false, "_retired": "<note>"` in that block only."""
    pattern = re.compile(r'("' + re.escape(block) + r'"\s*:\s*\{[^{}]*?"enabled"\s*:\s*)true')
    new, n = pattern.subn(
        lambda m: m.group(1) + 'false, "_retired": ' + json.dumps(note, ensure_ascii=True), text, 1
    )
    if n != 1:
        raise RuntimeError(f"could not find an enabled {block!r} block")
    return new


def _add_arms(text: str, param: str, arms: list) -> str:
    """`"<param>": { ... }` (an advice bound, the only place the param's value is an object) gains
    `"arms": [...]` before its closing brace, on its own last line's indent if it spans lines."""
    pattern = re.compile(r'("' + re.escape(param) + r'"\s*:\s*\{)([^{}]*?)(\s*\})')
    match = pattern.search(text)
    if match is None:
        raise RuntimeError(f"could not find the {param!r} bound")
    if '"arms"' in match.group(2):
        raise RuntimeError(f"the {param!r} bound already names other arms; edit it by hand")
    body = match.group(2).rstrip()
    addition = ', "arms": ' + json.dumps(arms)
    return text[: match.start(2)] + body + addition + match.group(3) + text[match.end(3) :]


def _bwb_declared_arms() -> dict[str, list]:
    """`{param: arms}` from this checkout's bwb config example -- the declaration bwb's own
    `tests/test_advice_arms.py` derives from management.evaluate. Empty before PR #224."""
    example = Path(__file__).resolve().parents[1] / "packages" / "bwb" / "config.example.json"
    try:
        bounds = json.loads(_read(example)).get("advice", {}).get("bounds", {})
    except (OSError, ValueError):
        return {}
    return {
        p: list(r["arms"])
        for p, r in bounds.items()
        if isinstance(r, dict) and isinstance(r.get("arms"), list)
    }


# --------------------------------------------------------------------------- the steps
def step_advisor(plan: Plan, h: Path) -> None:
    db = h / "data" / "advisor" / "advisor.db"
    if not db.is_file():
        plan.notes.append("advisor: no advisor.db; nothing to wind down")
        return
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        for exp_id, reason in KILLS.items():
            row = conn.execute(
                "SELECT name, status, sessions_run FROM experiments WHERE id = ?", (exp_id,)
            ).fetchone()
            if row is None:
                plan.notes.append(f"advisor: {exp_id} not found; skipped")
            elif row[1] in ("active", "queued"):
                plan.kills.append((exp_id, reason))
                plan.notes.append(f"advisor: kill {exp_id} ({row[0]}, {row[2]} sessions run)")
            else:
                plan.notes.append(f"advisor: {exp_id} ({row[0]}) already {row[1]}; nothing to do")
    finally:
        conn.close()


def step_flies(plan: Plan, h: Path, *, selector: bool, mid_first: bool) -> None:
    try:
        from cherrypick.flies import engine, live_orders
    except ImportError as exc:
        plan.refused.append(f"flies: cannot import the package ({exc})")
        return
    if "live-shadow" not in engine.ARMS or not hasattr(engine, "live_mirror_params"):
        plan.refused.append("flies: this checkout has no live-shadow arm; merge the boundary PR first")
        return
    if not set(FLIES_ADD) <= set(engine.ARMS) or not hasattr(engine, "placement_refusal"):
        plan.refused.append(
            "flies: this checkout has no range-centre / window-1300 arms; merge their PR first"
        )
        return
    if mid_first and not hasattr(live_orders, "entry_pricing_mode"):
        plan.refused.append("flies: this checkout has no live.entry_pricing; merge the pricing PR first")
        return
    path = h / "config" / "flies.json"

    def mutate(doc):
        arms = doc.setdefault("arms", {})
        for name, why in FLIES_RETIRE.items():
            if name in arms and arms[name].get("enabled", True):
                arms[name]["enabled"] = False
                arms[name]["_retired"] = (
                    f"Retired {BOUNDARY} ({JOURNAL}): {why}. Kept for its rows; never delete."
                )
        if "live-shadow" not in arms:
            arms["live-shadow"] = dict(LIVE_SHADOW)
        control = {k: v for k, v in (arms.get("control") or {}).items() if not k.startswith("_")}
        for name, (variable, what) in FLIES_ADD.items():
            if name not in arms:
                arms[name] = {
                    **control,
                    **variable,
                    "enabled": True,
                    "_note": f"From {BOUNDARY} (docs/experiment-log.md 2026-10-14): {what}. One variable vs "
                    "control: keep every other key identical to control's.",
                }
        if selector and "selector" in arms and not arms["selector"].get("enabled", True):
            arms["selector"]["enabled"] = True
        bounds = (doc.get("advice") or {}).get("bounds") or {}
        if "miss_stop_minutes" in bounds:
            del bounds["miss_stop_minutes"]
            doc["advice"]["_removed_bounds"] = (
                f"miss_stop_minutes: removed {BOUNDARY} ({JOURNAL}); negative on live, "
                "re-proposed repeatedly. "
                "engine.miss_stop_refusal stays for an arm to set."
            )
        if mid_first and doc.get("live", {}).get("entry_pricing") != "mid_first":
            doc["live"]["entry_pricing"] = "mid_first"

    change = _edit_json(path, mutate)
    if change:
        plan.files[path] = change
    if mid_first:
        plan.breaks.append(
            (
                h / "data" / "flies" / "live_trades.db",
                "scope",
                {
                    "break_date": BOUNDARY,
                    "scope": "*",
                    "kind": "entry_pricing",
                    "reason": "live entries priced mid first (live.entry_pricing mid_first): "
                    "the fresh mid on a "
                    "structure's first order of the day, conceding to mid - 0.125 x spread after one tick; "
                    "entry credits and fill rates either side of it are not poolable",
                    "detail": "docs/experiment-log.md 2026-10-12; "
                    "switched on by scripts/boundary_2026_10_19.py",
                },
            )
        )


def step_bwb(plan: Plan, h: Path) -> None:
    path = h / "config" / "bwb.json"
    notes = {
        "delta": "the 2-year modelled backtest put delta at -$9,983 against control's -$9,128 with a $24.9k "
        "drawdown; its paper lead is the regime. Open rows run off by expiry",
        "wall": "a $300/week edge would take ~126 weeks to read at its $1,353 weekly-difference sd. "
        "Open rows run off",
    }

    declared = _bwb_declared_arms()
    if not declared:
        plan.notes.append(
            "bwb: this checkout's config.example declares no advice-bound `arms` (PR #224 not merged); "
            "advice bounds left as they are"
        )

    def _arms_to_add(doc) -> dict[str, list]:
        bounds = (doc.get("advice") or {}).get("bounds") or {}
        return {
            param: arms
            for param, arms in declared.items()
            if isinstance(bounds.get(param), dict) and bounds[param].get("arms") != arms
        }

    def mutate(doc):
        for book, why in notes.items():
            blk = (doc.get("books") or {}).get(book)
            if isinstance(blk, dict) and blk.get("enabled", True):
                blk["enabled"] = False
                blk["_retired"] = f"Retired {BOUNDARY} ({JOURNAL}): {why}."
        for param, arms in _arms_to_add(doc).items():
            doc["advice"]["bounds"][param]["arms"] = list(arms)

    def patch(text):
        for book, why in notes.items():
            blk = (json.loads(text).get("books") or {}).get(book)
            if isinstance(blk, dict) and blk.get("enabled", True):
                text = _disable_block(text, book, f"Retired {BOUNDARY} ({JOURNAL}): {why}.")
        for param, arms in _arms_to_add(json.loads(text)).items():
            text = _add_arms(text, param, arms)
        return text

    doc = json.loads(_read(path))
    before = _roster(doc.get("books") or {}, ("control", "delta", "bounce", "flip"))
    change = _edit_json(path, mutate, patch)
    if change:
        plan.files[path] = change
        after = _roster(json.loads(change[1]).get("books") or {}, ("control", "delta", "bounce", "flip"))
        # An `arms` declaration on the advice bounds changes no book: it is the advisor's input
        # (its 2026-10-15 break, in packages/advisor/CLAUDE.md), never a bwb ledger break.
        if before != after:
            plan.breaks.append(
                _key_break(
                    h / "data" / "bwb" / "paper_trades.db",
                    "arms",
                    before,
                    after,
                    "delta and wall retired; their open rows are managed and settled to expiry. " + JOURNAL,
                )
            )


def step_curve(plan: Plan, h: Path) -> None:
    path = h / "config" / "curve.json"
    why = f"Retired {BOUNDARY} ({JOURNAL}): 0 hook signals in 33 sessions; it never traded."

    def mutate(doc):
        blk = (doc.get("books") or {}).get("hook")
        if isinstance(blk, dict) and blk.get("enabled", True):
            blk["enabled"] = False
            blk["_retired"] = why

    doc = json.loads(_read(path))
    builtin = ("control", "noflip", "hook", "near")
    before = _roster(doc.get("books") or {}, builtin)
    change = _edit_json(path, mutate, lambda t: _disable_block(t, "hook", why))
    if change:
        plan.files[path] = change
        after = _roster(json.loads(change[1]).get("books") or {}, builtin)
        plan.breaks.append(
            _key_break(
                h / "data" / "curve" / "paper_trades.db",
                "arms",
                before,
                after,
                "hook retired: 0 signals in 33 sessions, no position ever held. " + JOURNAL,
            )
        )


def step_calendars(plan: Plan, h: Path) -> None:
    path = h / "config" / "calendars.json"
    note = (
        f"time_exit: removed {BOUNDARY} ({JOURNAL}); exit_policies replays every time exit, Friday noon "
        "included, every run. Its experiment was concluded by advisor kill; open rows keep frozen params."
    )

    def mutate(doc):
        adv = doc.get("advice") or {}
        if "time_exit" in (adv.get("bounds") or {}):
            del adv["bounds"]["time_exit"]
            adv["_removed_bounds"] = note

    def patch(text):
        new, n = re.subn(r'\r?\n[ \t]*"time_exit"\s*:\s*\{[^{}]*\},[ \t]*(?=\r?\n)', "", text, count=1)
        if n != 1:
            raise RuntimeError("could not find the time_exit bound line")
        new, n = re.subn(
            r'(\r?\n)([ \t]*)("bounds"\s*:\s*\{)',
            lambda m: (
                m.group(1)
                + m.group(2)
                + '"_removed_bounds": '
                + json.dumps(note)
                + ","
                + m.group(1)
                + m.group(2)
                + m.group(3)
            ),
            new,
            count=1,
        )
        if n != 1:
            raise RuntimeError("could not find the advice bounds block")
        return new

    old_bounds = sorted(
        k
        for k in ((json.loads(_read(path)).get("advice") or {}).get("bounds") or {})
        if not k.startswith("_")
    )
    change = _edit_json(path, mutate, patch)
    if change:
        plan.files[path] = change
        new_bounds = [k for k in old_bounds if k != "time_exit"]
        plan.breaks.append(
            _key_break(
                h / "data" / "calendars" / "paper_trades.db",
                "advice_bounds",
                ", ".join(old_bounds),
                ", ".join(new_bounds),
                "time_exit removed: the read side replays it; advised:fri-noon-cycle-extension "
                "concluded by advisor kill, its open rows keep their frozen params. " + JOURNAL,
            )
        )


def step_earnings(plan: Plan, h: Path) -> None:
    try:
        from cherrypick.earnings import strat_test_harness as harness
    except ImportError as exc:
        plan.refused.append(f"earnings: cannot import the package ({exc})")
        return
    retired = getattr(harness, "STRAT_TEST_RETIRED", {})
    if retired.get("atm_calendar") != BOUNDARY or retired.get("double_calendar") != BOUNDARY:
        plan.refused.append(
            "earnings: this checkout does not retire the calendars on 10-19; merge the boundary PR first"
        )
        return
    plan.notes.append(
        "earnings: the retirement is dated in code (STRAT_TEST_RETIRED); only the break is written"
    )
    plan.breaks.append(
        _key_break(
            h / "data" / "earnings" / "paper_trades.db",
            "strat_test_strategies",
            "every registered strategy",
            "atm_calendar and double_calendar retired",
            "atm_calendar 56 trades 21% won -$6,349; double_calendar 35, 14%, -$3,660 (through 10-12). "
            "Their open rows run off under the ordinary close path; the advised dc-stop-at-75pct-debit "
            "twin was concluded by advisor kill. " + JOURNAL,
        )
    )


def step_meic(plan: Plan, h: Path) -> None:
    path = h / "config" / "meic.risk.json"

    def mutate(doc):
        profiles = doc.setdefault("profiles", {})
        for name, keys in MEIC_MINUS.items():
            if name not in profiles:
                gate = ", ".join(f"{k} {v!r}" for k, v in keys.items())
                profiles[name] = {
                    "enabled": True,
                    **keys,
                    "_note": f"From {BOUNDARY} ({JOURNAL}): live-shadow (the live top level) "
                    f"with ONE gate set to control's value ({gate}), to learn which gate keeps live "
                    "dark. Read against live-shadow.",
                }

    change = _edit_json(path, mutate)
    if change:
        plan.files[path] = change
        for name, keys in MEIC_MINUS.items():
            plan.breaks.append(
                (
                    h / "data" / "meic" / "paper_trades.db",
                    "scope",
                    {
                        "break_date": BOUNDARY,
                        "scope": name,
                        "kind": "arm_added",
                        "reason": f"{name} enters the roster: live-shadow with {', '.join(keys)} "
                        "at control's value",
                        "detail": "live-shadow filled 1 of 10 sessions 09-29..10-12; " + JOURNAL,
                    },
                )
            )


def _roster(books: dict, builtin: tuple) -> str:
    names = list(builtin) + [n for n in books if n not in builtin and not n.startswith("_")]
    on = [n for n in names if (books.get(n) or {}).get("enabled", n in builtin)]
    return ", ".join(on)


def _key_break(db: Path, key: str, old: str, new: str, note: str):
    return (db, "key", {"break_date": BOUNDARY, "key": key, "old_value": old, "new_value": new, "note": note})


# --------------------------------------------------------------------------- show and apply
def show(plan: Plan) -> None:
    for line in plan.notes:
        print("  " + line)
    for path, (old, new) in plan.files.items():
        print(f"\n--- {path}")
        diff = difflib.unified_diff(old.splitlines(), new.splitlines(), "before", "after", lineterm="", n=1)
        for line in diff:
            print(line[:300] + (" ..." if len(line) > 300 else ""))
    for db, _schema, row in plan.breaks:
        label = row.get("scope", row.get("key"))
        print(
            f"\n+++ break {db.parent.name}/{db.name}: {row['break_date']} {label} "
            f"{row.get('kind', '')} {row.get('new_value', row.get('reason', ''))[:140]}"
        )
    for exp_id, _ in plan.kills:
        print(f"\n!!! advisor kill {exp_id}")
    for line in plan.refused:
        print(f"\nREFUSED: {line}")


def backup_dir(h: Path) -> Path:
    d = h / "backups" / f"boundary-{BOUNDARY}-{time.strftime('%Y%m%d-%H%M%S')}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _backup_db(db: Path, dest: Path) -> None:
    src = sqlite3.connect(str(db))
    out = sqlite3.connect(str(dest / f"{db.parent.name}-{db.name}"))
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()


def apply(plan: Plan, h: Path) -> None:
    dest = backup_dir(h)
    for path in plan.files:
        shutil.copy2(path, dest / path.name)
    dbs = {db for db, _, _ in plan.breaks}
    if plan.kills:
        dbs.add(h / "data" / "advisor" / "advisor.db")
    for db in dbs:
        if db.is_file():
            _backup_db(db, dest)
    print(f"backed up to {dest}")

    # 1. kills first: a bound removed under a running experiment would reject, not conclude, it.
    for exp_id, reason in plan.kills:
        res = subprocess.run(
            [sys.executable, "-m", "cherrypick.advisor", "kill", exp_id, "--reason", reason],
            capture_output=True,
            text=True,
        )
        print(
            f"advisor kill {exp_id}: rc {res.returncode} "
            f"{res.stdout.strip()[:300]} {res.stderr.strip()[:300]}"
        )
        if res.returncode != 0:
            raise SystemExit("an advisor kill failed; stopping before any config change (rerun after fixing)")

    # 2. configs, each written whole (a crash leaves the old or the new file, never half of one).
    for path, (_old, new) in plan.files.items():
        tmp = path.with_suffix(path.suffix + ".boundary-tmp")
        tmp.write_bytes(new.encode("utf-8"))
        os.replace(tmp, path)
        print(f"wrote {path}")

    # 3. breaks, idempotent upserts in each ledger's own schema.
    for db, schema, row in plan.breaks:
        if not db.is_file():
            print(f"skip break: no ledger at {db}")
            continue
        conn = sqlite3.connect(str(db))
        try:
            if schema == "scope":
                conn.execute(
                    "INSERT INTO measurement_breaks (break_date, scope, kind, reason, detail, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(break_date, scope, kind) DO UPDATE SET "
                    "reason = excluded.reason, detail = COALESCE(excluded.detail, detail)",
                    (
                        row["break_date"],
                        row["scope"],
                        row["kind"],
                        row["reason"],
                        row["detail"],
                        dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO measurement_breaks "
                    "(break_date, key, old_value, new_value, note, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(break_date, key) DO UPDATE SET "
                    "old_value = excluded.old_value, new_value = excluded.new_value, note = excluded.note",
                    (
                        row["break_date"],
                        row["key"],
                        row["old_value"],
                        row["new_value"],
                        row["note"],
                        time.time(),
                    ),
                )
            conn.commit()
            print(f"break written: {db.parent.name}/{db.name} {row.get('scope', row.get('key'))}")
        finally:
            conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    ap.add_argument(
        "--enable-flies-mid-first",
        action="store_true",
        help="ALSO switch live flies entries to mid-first pricing "
        "(a live measurement break; the user's call)",
    )
    ap.add_argument("--skip-selector", action="store_true", help="leave the flies selector arm as it is")
    ap.add_argument("--allow-early", action="store_true", help=f"apply before {EARLIEST_APPLY}")
    args = ap.parse_args(argv)

    h = home()
    today = dt.date.today().isoformat()
    print(f"boundary {BOUNDARY}; home {h}; today {today}; {'APPLY' if args.apply else 'dry run'}")
    plan = Plan()
    step_advisor(plan, h)
    step_flies(plan, h, selector=not args.skip_selector, mid_first=args.enable_flies_mid_first)
    step_bwb(plan, h)
    step_curve(plan, h)
    step_calendars(plan, h)
    step_earnings(plan, h)
    step_meic(plan, h)
    show(plan)

    if not args.apply:
        print(f"\ndry run: nothing written. Re-run with --apply (from {EARLIEST_APPLY}).")
        return 1 if plan.refused else 0
    if plan.refused:
        print("\nrefusing to apply: a step's code is missing (see REFUSED above)")
        return 1
    if today < EARLIEST_APPLY and not args.allow_early:
        print(f"\nrefusing to apply before {EARLIEST_APPLY}: the new roster would trade this week's sessions")
        return 1
    if today >= BOUNDARY:
        print(f"\nnote: applying on or after {BOUNDARY}; the breaks stay dated {BOUNDARY} as declared")
    apply(plan, h)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
