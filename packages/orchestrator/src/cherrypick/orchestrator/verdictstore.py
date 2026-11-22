"""Study verdicts, recorded on this machine only (`data/research/verdicts.json` under the home).

The register (`research/hypotheses.json`) holds what each study declared. Its verdict is recorded here,
never in the repo: backtest results stay local. When a study's queue row finishes, the queue records
its verdict here (`record_finished`); `tools/record_verdict.py` records one by hand. `overlay` lays the
recorded verdicts over the register's `declared` rows, and `hypotheses.load` applies it, so every reader
(the `hypotheses` command, the Studies board, the scorecard, the retests) sees the recorded status. The
register check reads the repo file alone.

A study's verdict comes from the study kit's block in its result (`cherrypick.backtest.study.rule`): a
`rule` of the kit's keys and, per arm, its `n`, `parts` and `verdict`, which the kit computes from the
declared rule itself. An older script's verdict field can apply a check its declaration does not, so a
result without the kit's block is refused here and recorded by hand with its status and outcome.

Each record holds the status (`passed` when an arm passed, `no_verdict` when no arm was judged, else
`failed`), an outcome in words (each arm and its word, never a figure), the result file as a `{home}/`
path, when it was recorded and by what. A study is recorded once: a second record is refused, so a
re-run never rewrites a verdict silently.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import home as core_home
from cherrypick.core.jsonio import write_json_atomic

KIT_KEYS = {"t_bar", "min_n", "holdout_min_n", "tail", "delta", "splits"}
UNJUDGED = {"no verdict", "not read"}
STATUSES = ("passed", "failed", "no_verdict")
ITEM_STUDY = re.compile(r"^item\d+$")
RESULT_PREFIX = "{home}/"
ET = ZoneInfo("America/New_York")


class Refused(ValueError):
    """The study cannot be recorded this way."""


def path() -> Path:
    return core_home.data_dir("research") / "verdicts.json"


def read(p: Path | None = None) -> dict[str, dict]:
    """Every recorded verdict by study; empty when none is recorded or the file cannot be read."""
    try:
        doc = json.loads((p or path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    studies = doc.get("studies") if isinstance(doc, dict) else None
    return {k: v for k, v in (studies or {}).items() if isinstance(v, dict) and v.get("status") in STATUSES}


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


def home_path(p: Path | str, home: Path | None = None) -> str:
    """`p` as a `{home}/...` path; `Refused` when it is not under the home."""
    base = (home or core_home.home()).resolve()
    try:
        rel = Path(p).expanduser().resolve().relative_to(base)
    except ValueError as exc:
        raise Refused(f"the result {p} is not under the home") from exc
    return RESULT_PREFIX + rel.as_posix()


def record(
    study: str,
    status: str,
    outcome: str,
    result: str,
    *,
    by: str,
    p: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Write one study's verdict. Refuses a study already recorded, a status that is not a verdict, and
    a result that is not a `{home}/` path."""
    if not ITEM_STUDY.match(study or ""):
        raise Refused(f"{study!r} is not a study slug like item90")
    if status not in STATUSES:
        raise Refused(f"status is one of {', '.join(STATUSES)}")
    if not (isinstance(result, str) and result.startswith(RESULT_PREFIX) and result.endswith(".json")):
        raise Refused(f"the result must be a {RESULT_PREFIX}....json path")
    if not outcome or re.search(r"\d+\.\d|\$", outcome):
        # Words only: a figure from a study never goes into a record that is read back as text.
        raise Refused("the outcome is words only: no figures")
    target = p or path()
    try:
        doc = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {}
    studies = doc.get("studies") if isinstance(doc.get("studies"), dict) else {}
    if study in studies:
        raise Refused(f"{study} is recorded already ({studies[study].get('status')})")
    entry = {
        "status": status,
        "outcome": outcome,
        "results": [result],
        "recorded_at": (now or datetime.now(ET)).isoformat(timespec="seconds"),
        "by": by,
    }
    studies[study] = entry
    write_json_atomic(target, {"_note": __doc__.splitlines()[0], "studies": dict(sorted(studies.items()))})
    return {"study": study, **entry}


def record_finished(
    study: str, output: str | Path, doc: dict | None, *, task: int, p: Path | None = None
) -> dict:
    """The queue's record of a finished study row: its kit verdict from `doc`, the row's output as the
    result. `Refused` when the study is not an item, the row wrote no kit verdict, or it is recorded."""
    if not ITEM_STUDY.match(study or ""):
        raise Refused(f"{study} is not a register study")
    if not isinstance(doc, dict):
        raise Refused("the row wrote no result")
    verdicts = kit_verdicts(doc)
    return record(
        study, status_of(verdicts), outcome_of(verdicts), home_path(output), by=f"queue task {task}", p=p
    )


def overlay(reg: dict, recorded: dict[str, dict]) -> dict:
    """The register with each `declared` row that has a recorded verdict given its status, outcome and,
    where it lists none, its result. A row the repo already records keeps the repo's verdict."""
    rows = []
    for row in reg.get("hypotheses", []):
        rec = recorded.get(str(row.get("study")))
        if rec is not None and row.get("status") == "declared":
            row = {
                **row,
                "status": rec["status"],
                "outcome": rec.get("outcome"),
                "results": row.get("results") or list(rec.get("results") or []),
                "recorded": "local",
            }
        rows.append(row)
    return {**reg, "hypotheses": rows}
