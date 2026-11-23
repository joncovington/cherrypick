"""Recording a finished study's verdict in the register, on this machine (`hypotheses.register_path`).

The register and each study's Result paragraph live under the home, never in the repo. When a study's
queue row finishes, the queue records its verdict (`record_finished`); `tools/record_verdict.py` records
one by hand. A record sets the study's `declared` row's `status`, `outcome`, `results` (where the row
lists none), `recorded_at` and `recorded_by`, and writes its Result paragraph
(`hypotheses.results_path`) where there is none yet.

A study's verdict comes from the study kit's block in its result (`cherrypick.backtest.study.rule`): a
`rule` of the kit's keys and, per arm, its `n`, `parts` and `verdict`, which the kit computes from the
declared rule itself. An older script's verdict field can apply a check its declaration does not, so a
result without the kit's block is refused here and recorded by hand with its status and outcome.

The status is `passed` when an arm passed, `no_verdict` when no arm was judged, else `failed`; the
outcome is words (each arm and its word, never a figure); the result file is a `{home}/` path. A study
is recorded once: a row that is not `declared` is refused, so a re-run never rewrites a verdict.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from cherrypick.core import home as core_home
from cherrypick.core.clock import ET
from cherrypick.core.jsonio import write_json_atomic

KIT_KEYS = {"t_bar", "min_n", "holdout_min_n", "tail", "delta", "splits"}
UNJUDGED = {"no verdict", "not read"}
STATUSES = ("passed", "failed", "no_verdict")
ITEM_STUDY = re.compile(r"^item\d+$")
RESULT_PREFIX = "{home}/"

HEAD = {"passed": "pass", "failed": "fail", "no_verdict": "no verdict"}


class Refused(ValueError):
    """The study cannot be recorded this way."""


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
    arms = "; ".join(f"{a} {w}" for a, w in verdicts.items())
    return f"{HEAD[status_of(verdicts)]}: {arms}, by the declared rule"


def home_path(p: Path | str, home: Path | None = None) -> str:
    """`p` as a `{home}/...` path; `Refused` when it is not under the home."""
    # Compared as written, never through a link: a home's data folder may be a junction to another drive
    # (`data/backtest` here), and a resolved path would no longer sit under the home it is read from.
    base = Path(os.path.abspath(home or core_home.home()))
    try:
        rel = Path(os.path.abspath(Path(p).expanduser())).relative_to(base)
    except ValueError as exc:
        raise Refused(f"the result {p} is not under the home") from exc
    return RESULT_PREFIX + rel.as_posix()


def plain_paragraph(status: str, outcome: str, day: str) -> str:
    """A Result paragraph for a hand record: the verdict word, then the outcome as given."""
    return f"- **Result ({day}): {HEAD[status]}.**\n  {outcome}."


def record(
    study: str,
    status: str,
    outcome: str,
    result: str,
    *,
    by: str,
    paragraph: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Write one study's verdict into its register row, and its Result paragraph where it has none.
    Refuses a study that is not a `declared` row, a status that is not a verdict, an outcome with a
    figure, and a result that is not a `{home}/` path."""
    from . import hypotheses

    if not ITEM_STUDY.match(study or ""):
        raise Refused(f"{study!r} is not a study slug like item90")
    if status not in STATUSES:
        raise Refused(f"status is one of {', '.join(STATUSES)}")
    if not (isinstance(result, str) and result.startswith(RESULT_PREFIX) and result.endswith(".json")):
        raise Refused(f"the result must be a {RESULT_PREFIX}....json path")
    if not outcome or re.search(r"\d+\.\d|\$", outcome):
        raise Refused("the outcome is words only: no figures")
    target = hypotheses.register_path()
    reg = hypotheses.load()  # imports a row the plan declared but the register does not hold yet
    row = next((r for r in reg.get("hypotheses", []) if r.get("study") == study), None)
    if row is None:
        raise Refused(f"no register row for {study}")
    if row.get("status") != "declared":
        raise Refused(f"{study} is recorded already ({row.get('status')})")
    at = now or datetime.now(ET)
    row["status"] = status
    row["outcome"] = outcome
    row["results"] = row.get("results") or [result]
    row["recorded_at"] = at.isoformat(timespec="seconds")
    row["recorded_by"] = by
    write_json_atomic(target, reg, default=None)
    rpath = hypotheses.results_path(study)
    wrote = None
    if not rpath.exists():
        text = paragraph or plain_paragraph(status, outcome, at.date().isoformat())
        rpath.parent.mkdir(parents=True, exist_ok=True)
        rpath.write_text(text.rstrip() + "\n", encoding="utf-8")
        wrote = str(rpath)
    return {
        "study": study,
        "status": status,
        "outcome": outcome,
        "results": row["results"],
        "result_md": wrote,
    }


def record_finished(study: str, output: str | Path, doc: dict | None, *, item: int, by: str) -> dict:
    """The record of a finished study's kit result: its verdict from `doc`, `output` as the result, and
    the kit's drafted Result paragraph (`verdictdraft`). `Refused` when the study is not an item, the
    result carries no kit verdict, or it is recorded already."""
    from . import verdictdraft

    if not ITEM_STUDY.match(study or ""):
        raise Refused(f"{study} is not a register study")
    if not isinstance(doc, dict):
        raise Refused("the row wrote no result")
    verdicts = kit_verdicts(doc)
    result = home_path(output)
    day = datetime.now(ET).date().isoformat()
    where = "~/.cherrypick/" + result.removeprefix(RESULT_PREFIX).rsplit("/", 1)[0] + "/"
    try:
        drafted = verdictdraft.draft(item, doc, day=day, provisional=False, results_dir=where)["paragraph"]
    except (KeyError, TypeError, ValueError):
        drafted = None
    return record(study, status_of(verdicts), outcome_of(verdicts), result, by=by, paragraph=drafted)
