"""Item 84: item 73's MEIC 14:00 stacks re-judged on the three bars.

The declaration is research/edge-hypotheses-plan.md section 84; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

A retest of item 73, said out loud: the same replay, read from item 73's stored design run (its
result file names the run), never replayed again. Item 73 judged each extra condor's session net and
the larger arm's net over its own drawdown; it never charged the arms for the buying power they hold
or judged their tail against a year of their mean. This judges `n1` (live's rule), `n2` and `n3` each
through the study kit's `rule`:

- **capital:** each session's net over the buying power its condors hold (`(width - credit) x 100 x
  quantity`, summed, item 73's `session_bp`), less the 3-month bill on it for the day, above zero;
- **tail:** the 99th-percentile one-year drawdown of those session returns under one year of their
  mean;
- **delta:** not applicable. A condor is entered near delta-neutral, so financing its entry delta
  is the bill alone, which the capital bar already charges.

One observation is one session in which the arm entered (a session it held no condor holds no
capital). Item 73 read the design window, so the verdict is provisional by declaration, and MEIC
history never unlocked, so it is provisional twice. Loading item 73's helpers is the point of a
retest of its run, not a shared piece the kit should hold.

- `study`: writes `{results}/results.json`, the kit's verdict block per arm.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date
from pathlib import Path

from cherrypick.core import treasury

from cherrypick.backtest import paths
from cherrypick.backtest.study import rule as kit_rule

sys.path.insert(0, str(Path(__file__).parent))
from meic_1400_stack_item73 import by_arm_day, session_bp  # noqa: E402
from meic_xsp_item72 import ledger_rows, session_rows  # noqa: E402

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: t of 2.3 keeps a Holm step across the three arms from binding: at 100 sessions or more its
#: one-sided p is under 0.0167, Holm's strictest bar for three.
RULE = {
    "t_bar": 2.3,
    "min_n": 100,
    "tail": {"per_year": "sample", "resamples": 10_000, "seed": 84},
    "delta": False,
    "splits": [],
}

CHAIN = ({"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},)

ARMS = ("n1", "n2", "n3")
#: Item 73's stored design run, as its result file records it.
ITEM73_RESULT = "study-design-2022-05-16-2025-06-30.json"
#: A 0DTE condor holds its buying power for the session: one calendar day of the bill.
DAYS_HELD = 1
RATE_STALE_DAYS = 10


def out_dir() -> Path:
    d = paths.results_dir() / "research" / "item84"
    d.mkdir(parents=True, exist_ok=True)
    return d


def item73_run(results_dir: Path | None = None) -> str:
    f = (results_dir or paths.results_dir()) / "research" / "item73" / ITEM73_RESULT
    if not f.exists():
        raise SystemExit(f"no item 73 result at {f}: item 73's design run is what this re-reads")
    return json.loads(f.read_text(encoding="utf-8"))["run_id"]


def arm_rows(sessions: dict[str, dict], ledger: dict[str, list[dict]], rate) -> list[dict]:
    """The kit's rows for one arm: each session it entered, its net over the buying power its condors
    held. `sessions` is {day: {net, ...}} from the run's session results; `ledger` {day: [entries]}."""
    out = []
    for day in sorted(ledger):
        bp = session_bp(ledger[day])
        if bp <= 0 or day not in sessions or sessions[day].get("net") is None:
            continue
        out.append({"date": day, "net": sessions[day]["net"] / bp, "bp": 1.0, "days": DAYS_HELD,
                    "rate": rate(day), "session_bp": bp})  # fmt: skip
    return out


def reported(rows: list[dict]) -> dict:
    if not rows:
        return {"sessions": 0}
    bps = [r["session_bp"] for r in rows]
    return {"sessions": len(rows), "session_bp_median": statistics.median(bps), "session_bp_max": max(bps)}


def rate_reader():
    seen: dict[str, float] = {}

    def rate(session: str) -> float:
        if session not in seen:
            got = treasury.yield_before(session, "3m")
            if got is None or (date.fromisoformat(session) - got[0]).days > RATE_STALE_DAYS:
                raise SystemExit(f"no Treasury 3-month yield within {RATE_STALE_DAYS} days before {session}")
            seen[session] = got[1] / 100.0
        return seen[session]

    return rate


def study(run_id: str, rate) -> dict:
    sess = session_rows(run_id)
    ledger = by_arm_day(
        [r for r in ledger_rows(paths.results_dir() / "runs" / run_id) if r.get("arm") in ARMS]
    )
    rows = {a: arm_rows(sess.get(a, {}), ledger.get(a, {}), rate) for a in ARMS}
    block = kit_rule.evaluate(RULE, rows)
    return {**block, "run_id": run_id, "reported": {a: reported(rows[a]) for a in ARMS}}


def cmd_study(args) -> int:
    run_id = item73_run()
    doc = {
        "licence": "derived from licensed vendor data: never commit or publish",
        "item": 84,
        "retest_of": 73,
        "provisional": True,
        **study(run_id, rate_reader()),
        "corrections": list(REPLAY_CORRECTIONS),
    }
    path = Path(args.out) if args.out else out_dir() / "results.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    p = sub.add_parser("study")
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_study)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
