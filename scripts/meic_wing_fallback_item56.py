"""Item 56, the wide wing when its floor allows, else live's 10 (docs/edge-hypotheses-plan.md): `pm-200`
entering once a day, 14:00-14:30, at 15 (or 20) points wide at the first tick that width's gates all
pass, and at 10 wide at the first tick 10's gates pass if that comes first.

    python scripts/meic_wing_fallback_item56.py --window design|holdout [--json PATH]

**No new replay.** Item 50 replayed `g15` and `g20` (live's rule, gates and all, at 15 and 20 wide)
over the same ticks as `live-1400` (live's rule at 10). Each enters at most once a session, at the
first tick its gates pass, and nothing else differs between them. So the fallback rule is those
recorded entries composed tick by tick: in each session, the width whose first passing tick comes
first, the wide one on a tie. It is never chosen per session by outcome: a session's choice reads only
the two entry times, which the loop would know at the tick it entered.

The design window composes item 50's design run; the holdout composes item 50's holdout run with item
23's recorded `live-1400` holdout sessions, as item 50 does. Writes under the backtest data dir
(results/research/item56/), never into the repo: the inputs are licensed.

**The verdict** is item 50's, per arm (`f15`, `f20`): the paired difference against `live-1400`
(one session one observation, no entry at zero) with its mean above zero and a one-sided t above 2,
Holm across the two arms, positive in both time-halves; the arm's own net above zero and still positive
at 1.5x fees; its net over buying power at least `live-1400`'s. Then the holdout's paired difference
with a mean above zero and a t above 2. At least 100 sessions. The arithmetic is item 43's and item
50's, loaded from their scripts so the items cannot drift apart.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

from cherrypick.backtest import paths

_spec = importlib.util.spec_from_file_location(
    "meic_wing_width_gated_item50", Path(__file__).with_name("meic_wing_width_gated_item50.py")
)
i50 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(i50)
i43 = i50.i43

BASE = i50.BASE  # "live-1400"
#: Each judged arm and the item 50 arm it takes when its width qualifies first.
FALLBACK = {"f15": "g15", "f20": "g20"}
WIDTHS = {"f15": 15, "f20": 20}

#: Replay corrections to this study (docs/conventions/working-rules.md). None yet.
REPLAY_CORRECTIONS: list[dict] = []


def entry_times(ledger_dir: Path, arms: set[str]) -> dict[str, dict[str, str]]:
    """Each arm's entry time per session, `{arm: {session: entry_time}}`, from a run's ledgers.
    Refuses a session with more than one entry: the composition assumes one a session."""
    out: dict[str, dict[str, str]] = {}
    for db in sorted(ledger_dir.glob("*.db")):
        conn = sqlite3.connect(db)
        try:
            for arm, day, when in conn.execute("SELECT arm, trade_date, entry_time FROM ic_trades"):
                if arm not in arms:
                    continue
                seen = out.setdefault(arm, {})
                if day in seen:
                    raise SystemExit(
                        f"{arm} entered twice on {day}: the fallback assumes one entry a session"
                    )
                seen[day] = when
        finally:
            conn.close()
    return out


def compose(
    wide: dict[str, dict], base: dict[str, dict], wide_at: dict[str, str], base_at: dict[str, str]
) -> tuple[dict[str, dict], dict[str, str]]:
    """The fallback rule's sessions and which width each took ("wide", "base" or "none").

    `wide`/`base` are session rows `{day: {net, fees, trades}}` over the same sessions; `*_at` the
    entry time each entered at. The width whose gates passed first is taken, the wide one on a tie;
    a session neither entered is a no-entry session at zero, as both runs record it."""
    sessions, took = {}, {}
    for day in sorted(set(wide) | set(base)):
        w, b = wide_at.get(day), base_at.get(day)
        if w is not None and (b is None or w <= b):
            sessions[day], took[day] = wide[day], "wide"
        elif b is not None:
            sessions[day], took[day] = base[day], "base"
        else:
            sessions[day] = base.get(day) or wide[day]
            took[day] = "none"
    return sessions, took


def composed_risk(risk: dict[str, dict[str, float]], took: dict[str, str], wide_arm: str) -> dict[str, float]:
    """The buying power of the entry each session took."""
    out = {}
    for day, which in took.items():
        if which == "wide":
            out[day] = risk.get(wide_arm, {}).get(day, 0.0)
        elif which == "base":
            out[day] = risk.get(BASE, {}).get(day, 0.0)
    return out


def counts(took: dict[str, str], wide_at: dict[str, str], base_at: dict[str, str]) -> dict:
    """How the fallback split: sessions taken wide, taken at 10, and at 10 though the wide width
    entered later in the window (the floor refused it at the tick 10 qualified)."""
    later = sum(1 for d, w in took.items() if w == "base" and d in wide_at)
    return {
        "sessions": len(took),
        "took_wide": sum(w == "wide" for w in took.values()),
        "took_base": sum(w == "base" for w in took.values()),
        "no_entry": sum(w == "none" for w in took.values()),
        "base_while_wide_later": later,
    }


def item23_run_id() -> str | None:
    files = sorted((paths.results_dir() / "research" / "item23").glob("study-holdout-*.json"))
    files = [f for f in files if not f.name.endswith(".version.json")]
    return json.loads(files[-1].read_text(encoding="utf-8")).get("run_id") if files else None


def item50_doc(window: str) -> dict:
    files = sorted((paths.results_dir() / "research" / "item50").glob(f"study-{window}-*.json"))
    files = [f for f in files if not f.name.endswith(".version.json")]
    if not files:
        raise SystemExit(f"item 50 has no {window} result to compose: run it first")
    return json.loads(files[-1].read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--window", choices=("design", "holdout"), required=True)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    doc50 = item50_doc(args.window)
    start, end = doc50["span"]
    runs = paths.results_dir() / "runs"
    wide_run = doc50["run_id"]
    base_run = wide_run if args.window == "design" else item23_run_id()
    if base_run is None:
        raise SystemExit("item 23 has no recorded live-1400 holdout to compose with")

    wide_rows = i43.session_rows(wide_run)
    base_rows = i43.session_rows(base_run).get(BASE, {})
    times = entry_times(runs / wide_run, set(FALLBACK.values()) | ({BASE} if base_run == wide_run else set()))
    if base_run != wide_run:
        times.update(entry_times(runs / base_run, {BASE}))
    ledger = i43.ledger_rows(runs / wide_run) + (
        [] if base_run == wide_run else i43.ledger_rows(runs / base_run)
    )
    risk = i43.risk_by_session(ledger)

    tests, split = {}, {}
    for arm, src in FALLBACK.items():
        wide = wide_rows.get(src, {})
        sessions, took = compose(wide, base_rows, times.get(src, {}), times.get(BASE, {}))
        risk[arm] = composed_risk(risk, took, src)
        split[arm] = counts(took, times.get(src, {}), times.get(BASE, {}))
        tests[arm] = {
            "own": i43.own(sessions, start, end),
            "vs_base": i43.paired(sessions, base_rows, start, end),
            "per_risk": i50.per_risk(sessions, base_rows, risk, arm),
            # Reported only: the fallback against item 50's gated wide arm alone.
            "vs_gated_wide": i43.paired(sessions, wide, start, end),
        }
    i50.judge(tests)
    out_dir = paths.results_dir() / "research" / "item56"
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "item": 56,
        "window": args.window,
        "span": [start, end],
        "run_id": wide_run,
        "live_1400_run_id": base_run,
        "provisional": True,
        "split": split,
        "reference": {BASE: i43.own(base_rows, start, end)},
        "arms": tests,
        "verdict": "pass" if any(v.get("verdict") == "pass" for v in tests.values()) else "fail",
    }
    path = Path(args.json) if args.json else out_dir / f"study-{args.window}-{start}-{end}.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"],
                      **{a: v.get("verdict") for a, v in tests.items()}}, indent=1))  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
