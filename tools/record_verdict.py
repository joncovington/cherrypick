"""Record a finished study's verdict on this machine, by hand (`cherrypick.orchestrator.verdictstore`).

    python tools/record_verdict.py item90 --dry-run    # what would be recorded, printed
    python tools/record_verdict.py item90              # record it
    python tools/record_verdict.py item90 --status failed --outcome "fail: ..."   # no kit block

The research queue records a study itself when its `study` row finishes; this is for a study it could
not record (a result without the study kit's verdict block) and for one that finished before it did.
Verdicts are recorded under the home, never in the repo: the register (`research/hypotheses.json`) keeps
the declaration, and every reader lays the recorded verdict over it.

The study's register row must still read `declared`. Its result is the row's first `results` path, else
the newest finished `study*` queue row's output, else `--result`. With the kit's block the status and
outcome come from it (`verdictstore.status_of`, `outcome_of`); without it, `--status` and `--outcome`
(words only, read the declared rule, never a figure) are required.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _queue_output(study: str) -> Path | None:
    """The newest finished `study*` queue row's output for `study`, when it exists."""
    from cherrypick.orchestrator import researchqueue

    for t in researchqueue.rows(study=study, limit=200):
        if str(t.get("step") or "").startswith("study") and t.get("status") == "done" and t.get("output"):
            p = Path(t["output"]).expanduser()
            if p.is_file():
                return p
    return None


def main(argv: list[str] | None = None) -> int:
    from cherrypick.core import home
    from cherrypick.orchestrator import hypotheses, verdictstore

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("study", help="the study's slug, e.g. item90")
    p.add_argument("--result", help="the result file, when neither the register nor the queue names it")
    p.add_argument("--status", choices=verdictstore.STATUSES, help="for a result without the kit's block")
    p.add_argument("--outcome", help="for a result without the kit's block: words only, by the declared rule")
    p.add_argument("--dry-run", action="store_true", help="print what would be recorded; write nothing")
    a = p.parse_args(argv)
    try:
        if not verdictstore.ITEM_STUDY.match(a.study):
            raise verdictstore.Refused(f"{a.study!r} is not a study slug like item90")
        reg = hypotheses.load(local=False)
        row = next((h for h in reg["hypotheses"] if h.get("study") == a.study), None)
        if row is None:
            raise verdictstore.Refused(f"no register row for {a.study}")
        if row.get("status") != "declared":
            raise verdictstore.Refused(f"the register already records {a.study} ({row.get('status')})")
        listed = [home.home() / r.removeprefix(verdictstore.RESULT_PREFIX) for r in row.get("results") or []]
        result = (
            Path(a.result).expanduser() if a.result else (listed[0] if listed else _queue_output(a.study))
        )
        if result is None or not result.is_file():
            raise verdictstore.Refused(f"no result file found for {a.study}: name it with --result")
        doc = json.loads(result.read_text(encoding="utf-8"))
        if a.status or a.outcome:
            if not (a.status and a.outcome):
                raise verdictstore.Refused("a hand record gives both --status and --outcome")
            status, outcome = a.status, a.outcome
        else:
            verdicts = verdictstore.kit_verdicts(doc)
            status, outcome = verdictstore.status_of(verdicts), verdictstore.outcome_of(verdicts)
        rel = verdictstore.home_path(result)
        if a.study in verdictstore.read():
            raise verdictstore.Refused(f"{a.study} is recorded already")
    except (verdictstore.Refused, OSError, json.JSONDecodeError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"study": a.study, "status": status, "outcome": outcome, "result": rel}, indent=1))
    if a.dry_run:
        return 0
    try:
        verdictstore.record(a.study, status, outcome, rel, by="by hand")
    except verdictstore.Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(f"recorded in {verdictstore.path()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
