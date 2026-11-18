"""Record a finished study's verdict in the register, and open the docs PR.

    python tools/record_verdict.py item90            # the edit, a branch, a commit and a PR
    python tools/record_verdict.py item90 --dry-run  # the edit only, printed

The study's register row (`research/hypotheses.json`) must still read `declared`. Its result file (the
row's first `results` path, `{home}` resolved) must carry the study kit's verdict block
(`cherrypick.backtest.study.rule`): a `rule` of the kit's keys and, per arm, its `n`, `parts` and
`verdict`. Those scripts compute the verdict from the declared rule itself. An older script's verdict
field can apply a check its declaration does not, so a result without the kit's block is refused and
stays a hand edit.

The edit sets two fields and nothing else:

- `status`: `passed` when an arm passed, `no_verdict` when no arm was judged, else `failed`;
- `outcome`: the status, then each arm and its verdict word. Words only: never a figure, so nothing
  derived from licensed data reaches the repo.

The plan section's Result paragraph stays a written addition. The PR is made from a new worktree off
`origin/main`, never the live checkout, and the worktree is removed once pushed. The register is
checked (`orchestrator.hypotheses.check`) before anything is committed.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REGISTER = Path("research/hypotheses.json")
STUDY = re.compile(r"^item\d+$")
KIT_KEYS = {"t_bar", "min_n", "holdout_min_n", "tail", "delta", "splits"}
UNJUDGED = {"no verdict", "not read"}


class Refused(ValueError):
    """The study cannot be recorded by this tool."""


def kit_verdicts(doc: dict) -> dict[str, str]:
    """Each arm's verdict word from the kit's block; `Refused` when the result does not carry one."""
    rule, arms, verdict = doc.get("rule"), doc.get("arms"), doc.get("verdict")
    if not (isinstance(rule, dict) and set(rule) <= KIT_KEYS and {"t_bar", "min_n"} <= set(rule)):
        raise Refused("the result carries no study-kit rule: record it by hand")
    if not (
        isinstance(arms, dict) and arms and all({"n", "parts", "verdict"} <= set(a) for a in arms.values())
    ):
        raise Refused("the result carries no study-kit arms block: record it by hand")
    if not (isinstance(verdict, dict) and set(verdict) == set(arms)):
        raise Refused("the result's verdict does not name each arm: record it by hand")
    return {a: str(w) for a, w in verdict.items()}


def status_of(verdicts: dict[str, str]) -> str:
    words = set(verdicts.values())
    if "pass" in words:
        return "passed"
    if words <= UNJUDGED:
        return "no_verdict"
    return "failed"


def outcome_of(verdicts: dict[str, str]) -> str:
    status = status_of(verdicts)
    head = {"passed": "pass", "failed": "fail", "no_verdict": "no verdict"}[status]
    arms = "; ".join(f"{a} {w}" for a, w in verdicts.items())
    return f"{head}: {arms}, by the declared rule"


def row_span(text: str, study: str) -> tuple[int, int]:
    """Where the register row for `study` sits in the file's text: from its `"id"` to its closing brace."""
    at = text.find(f'"study": "{study}"')
    if at < 0:
        raise Refused(f"no register row for {study}")
    start = text.rfind("{", 0, at)
    depth, i = 0, start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
        i += 1
    raise Refused("the register's row does not close")


def edit(text: str, study: str, verdicts: dict[str, str]) -> str:
    """The register's text with `study`'s status and outcome set, its formatting kept."""
    lo, hi = row_span(text, study)
    row = text[lo:hi]
    if '"status": "declared"' not in row:
        raise Refused(f"{study}'s row is not `declared`: it is recorded already")
    if '"outcome": null' not in row:
        raise Refused(f"{study}'s row already has an outcome")
    row = row.replace('"status": "declared"', f'"status": "{status_of(verdicts)}"', 1)
    row = row.replace(
        '"outcome": null', f'"outcome": {json.dumps(outcome_of(verdicts), ensure_ascii=False)}', 1
    )
    return text[:lo] + row + text[hi:]


def result_path(row: dict) -> Path:
    from cherrypick.core import home

    paths = row.get("results") or []
    if not paths:
        raise Refused(f"{row['study']} names no result file")
    return Path(str(paths[0]).replace("{home}", str(home.home())))


def _run(argv: list[str], cwd: Path) -> str:
    return subprocess.run(argv, cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8").stdout


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("study", help="the study's slug, e.g. item90")
    p.add_argument("--dry-run", action="store_true", help="print the edit; no branch, commit or PR")
    a = p.parse_args(argv)
    if not STUDY.match(a.study):
        print(f"refused: {a.study!r} is not a study slug like item90", file=sys.stderr)
        return 2
    try:
        reg = json.loads((REPO / REGISTER).read_text(encoding="utf-8"))
        row = next((h for h in reg["hypotheses"] if h.get("study") == a.study), None)
        if row is None:
            raise Refused(f"no register row for {a.study}")
        doc = json.loads(result_path(row).read_text(encoding="utf-8"))
        verdicts = kit_verdicts(doc)
        edit((REPO / REGISTER).read_text(encoding="utf-8"), a.study, verdicts)  # refuses a recorded row
    except (Refused, OSError, json.JSONDecodeError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    status, outcome = status_of(verdicts), outcome_of(verdicts)
    print(json.dumps({"study": a.study, "status": status, "outcome": outcome}, indent=1))
    if a.dry_run:
        return 0

    from cherrypick.orchestrator import hypotheses

    branch = f"research/record-{a.study}"
    wt = Path(tempfile.mkdtemp(prefix=f"record-{a.study}-"))
    shutil.rmtree(wt)
    _run(["git", "fetch", "-q", "origin", "main"], REPO)
    _run(["git", "worktree", "add", "-q", "-b", branch, str(wt), "origin/main"], REPO)
    try:
        target = wt / REGISTER
        target.write_text(edit(target.read_text(encoding="utf-8"), a.study, verdicts), encoding="utf-8")
        problems = hypotheses.check(json.loads(target.read_text(encoding="utf-8")), root=wt)
        if problems:
            print("refused: the register check fails:\n  " + "\n  ".join(problems), file=sys.stderr)
            return 1
        title = f"research: record {a.study}, {status.replace('_', ' ')}"
        body = (
            f"{a.study}'s queue run finished; its result carries the study kit's verdict block, computed "
            f"from the declared rule.\n\n- status: `{status}`\n- outcome: {outcome}\n\nWords only: the "
            "figures stay in the local result. The plan section's Result paragraph is a written addition."
        )
        _run(["git", "add", str(REGISTER)], wt)
        _run(["git", "commit", "-q", "-m", f"{title}\n\n{outcome}."], wt)
        _run(["git", "push", "-q", "-u", "origin", branch], wt)
        print(_run(["gh", "pr", "create", "--title", title, "--body", body], wt).strip())
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=REPO, capture_output=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
