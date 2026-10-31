"""`cherrypick hypotheses` — the hypothesis register and its yearly re-test (B7, B14).

`docs/hypotheses.json` holds one row per hypothesis: its four parts (what is measured, the expected
outcome as a number, the window and sample, the pass-or-fail rule), the mechanism, the rival
explanations with the check that rules each out, its status and a pointer to the plan section.
Never figures: licensed results stay under `~/.cherrypick`, so the register can live in the repo.

- `check`: every row is well formed, and every pointer names a heading that exists. Rows from item 18
  on must fill every field, and one with a verdict names its result files; legacy rows (1-17) point
  to the prose that holds theirs. On this machine the command also lists queue studies with no row
  (`unregistered`).
- `similar`: before a new hypothesis is declared, the rejected rows with the same setup, side,
  structure and window, so a retest is a choice the declaration says out loud.
- `rereads`: every logged rule change, and whether it has had its first `REREAD_AFTER` trades and is
  due a re-read (the counts come from the module's ledger).
- `retests --year Y`: B14. Every declared, passed or failed row re-run unchanged on year Y, as
  research-queue rows `<study>/retest-<Y>`. A row with no recipe is listed as needing one. A
  re-test only confirms, weakens or prompts a new declaration: it never changes a past verdict.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from cherrypick.core import home as core_home

from .researchqueue import repo_root

#: `retired` (2026-10-30): declared and closed without a run, its reason in the plan section. It has
#: no verdict and no result files, sits with the finished rows, and is never re-tested.
STATUSES = ("observation", "declared", "running", "passed", "failed", "no_verdict", "retired")
REJECTED = ("failed",)
RETESTABLE = ("declared", "passed", "failed", "no_verdict")
MATCH_ON = ("setup", "side", "structure", "window")
FOUR_PARTS = ("measure", "expected", "sample", "rule")
REQUIRED_NEW = (*FOUR_PARTS, "mechanism", *MATCH_ON)
VERDICTS = ("passed", "failed", "no_verdict")
RESULT_PREFIX = "{home}/"
ITEM_STUDY = re.compile(r"^item\d+$")
REREAD_AFTER = 100
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def register_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "docs" / "hypotheses.json"


def load(path: Path | None = None) -> dict:
    return json.loads((path or register_path()).read_text(encoding="utf-8"))


def _anchor(heading: str) -> str:
    s = re.sub(r"[^\w\s-]", "", heading.strip().lower())
    return re.sub(r"\s+", "-", s)


def anchors(md: str) -> set[str]:
    return {_anchor(m.group(1)) for m in re.finditer(r"^#{1,6} (.+)$", md, re.M)}


def check(reg: dict, *, root: Path | None = None) -> list[str]:
    """Problems with the register; empty means it is sound."""
    root = root or repo_root()
    problems, seen_ids, seen_studies = [], set(), set()
    docs: dict[str, set[str]] = {}
    for row in reg.get("hypotheses", []):
        rid = row.get("id")
        tag = f"row {rid}"
        if not isinstance(rid, int) or rid in seen_ids:
            problems.append(f"{tag}: id must be a unique integer")
        seen_ids.add(rid)
        study = row.get("study")
        if not isinstance(study, str) or not SLUG.match(study) or study in seen_studies:
            problems.append(f"{tag}: study must be a unique lowercase slug")
        seen_studies.add(study)
        if row.get("status") not in STATUSES:
            problems.append(f"{tag}: status {row.get('status')!r} is not one of {', '.join(STATUSES)}")
        if not row.get("legacy"):
            missing = [k for k in REQUIRED_NEW if not row.get(k)]
            if missing:
                problems.append(f"{tag}: a new hypothesis must state {', '.join(missing)}")
            rivals = row.get("rivals") or []
            if not rivals or not all(
                isinstance(r, dict) and r.get("explanation") and r.get("check") for r in rivals
            ):
                problems.append(f"{tag}: a new hypothesis names its rival explanations, each with its check")
            if row.get("status") in VERDICTS and not row.get("results"):
                problems.append(f"{tag}: a verdict names its result files in results")
        results = row.get("results")
        if results is not None and not (
            isinstance(results, list)
            and all(isinstance(p, str) and p.startswith(RESULT_PREFIX) for p in results)
        ):
            # Results are licensed or derived from licensed data: they live under the home, never the repo.
            problems.append(f"{tag}: results must be a list of {RESULT_PREFIX}... paths")
        if row.get("outcome") is not None and not isinstance(row.get("outcome"), str):
            problems.append(f"{tag}: outcome is the decision in words")
        pointer = str(row.get("pointer") or "")
        file, _, frag = pointer.partition("#")
        if not file or not frag:
            problems.append(f"{tag}: pointer must be <file>#<heading>")
            continue
        if file not in docs:
            try:
                docs[file] = anchors((root / file).read_text(encoding="utf-8"))
            except OSError:
                docs[file] = set()
        if frag not in docs[file]:
            problems.append(f"{tag}: pointer {pointer} names no heading")
        recipe = row.get("retest")
        if recipe is not None and not (
            isinstance(recipe, dict) and recipe.get("kind") and isinstance(recipe.get("args"), dict)
        ):
            problems.append(f"{tag}: retest must be {{kind, args}} as the research queue takes them")
    for i, rc in enumerate(reg.get("rule_changes", [])):
        missing = [k for k in ("date", "module", "arm", "change", "evidence") if not rc.get(k)]
        if missing:
            problems.append(f"rule change {i}: must state {', '.join(missing)}")
    return problems


def unregistered(reg: dict, queue_studies) -> list[str]:
    """Research-queue studies named `itemN` with no register row: a study ran before it was declared here."""
    known = {r.get("study") for r in reg.get("hypotheses", [])}
    return sorted({s for s in queue_studies if ITEM_STUDY.match(s or "") and s not in known})


GROUPS = ("in_progress", "declared", "finished", "observation")
ACTIVE = ("queued", "running")
BOARD_FIELDS = (
    "id", "study", "title", "status", "legacy", "pointer", "outcome",
    "measure", "expected", "sample", "rule", "mechanism", "rivals",
)  # fmt: skip


def _word(v: str) -> str:
    return v.strip().lower().replace("_", " ")


def verdicts(data) -> list[dict] | None:
    """A result file's `verdict` as `[{arm, verdict}]`, from the three shapes studies write: one word,
    one word per arm, or per arm as `{"verdict": ...}`. None when there is no word to read: the
    board says so and never works one out from the figures."""
    v = data.get("verdict") if isinstance(data, dict) else None
    if isinstance(v, str):
        return [{"arm": None, "verdict": _word(v)}]
    if not isinstance(v, dict) or not v:
        return None
    out = []
    for arm, x in v.items():
        word = x if isinstance(x, str) else x.get("verdict") if isinstance(x, dict) else None
        if not isinstance(word, str):
            return None
        out.append({"arm": arm, "verdict": _word(word)})
    return out


def _result(path: str, read, notes) -> dict:
    data, mtime = read(path)
    words = verdicts(data) if data is not None else None
    window, span = (data or {}).get("window"), (data or {}).get("span")
    if isinstance(window, list):
        window, span = None, window
    distinct = sorted({w["verdict"] for w in words or []})
    return {
        "path": path,
        "name": path.rsplit("/", 1)[-1],
        "present": data is not None,
        "modified": mtime,
        "window": window if isinstance(window, str) else None,
        "span": span if isinstance(span, list) and len(span) == 2 else None,
        "run_id": (data or {}).get("run_id"),
        "provisional": (data or {}).get("provisional"),
        "verdicts": words,
        "summary": None if not distinct else distinct[0] if len(distinct) == 1 else "mixed",
        "notes": notes(path),
    }


def board(reg: dict, queue_rows: list[dict], read, notes=lambda path: None) -> dict:
    """The studies board (docs/studies-board-plan.md): each register row joined with its result
    files' verdicts and its research-queue rows. `read(path)` returns `(data, modified)` for a
    `{home}/...` path, `(None, None)` when the file is absent or unreadable. `notes(path)` returns
    the Markdown a study left beside that file (`results.json` -> `results.md`), or None.

    The register decides the status. Where the queue disagrees (a declared row with a task running)
    both are shown; the reader never rewrites the register."""
    by_study: dict[str, list[dict]] = {}
    for q in queue_rows:
        by_study.setdefault(q.get("study") or "", []).append(q)
    rows = []
    for r in reg.get("hypotheses", []):
        tasks = sorted(by_study.get(r.get("study"), []), key=lambda q: q.get("id") or 0, reverse=True)
        results = [_result(p, read, notes) for p in r.get("results") or []]
        active = any(q.get("status") in ACTIVE for q in tasks)
        status = r.get("status")
        if status in VERDICTS or status == "retired":
            group = "finished"
        elif status == "observation":
            group = "observation"
        else:
            group = "in_progress" if active or status == "running" else "declared"
        stamps = [q.get(k) for q in tasks for k in ("finished_at", "started_at", "created_at") if q.get(k)]
        stamps += [x["modified"] for x in results if x["modified"]]
        rows.append({
            **{k: r.get(k) for k in BOARD_FIELDS},
            "group": group,
            "results": results,
            "queue": {
                "active": active,
                "tasks": [
                    {k: q.get(k) for k in ("id", "step", "status", "created_at", "started_at", "finished_at")}
                    for q in tasks[:10]
                ],
            },
            "last_activity": max(stamps) if stamps else None,
        })  # fmt: skip
    rows.sort(key=lambda x: x["id"] or 0, reverse=True)
    rows.sort(key=lambda x: x["last_activity"] or "", reverse=True)
    rows.sort(key=lambda x: GROUPS.index(x["group"]))
    return {"rows": rows, "unregistered": unregistered(reg, by_study)}


def similar(reg: dict, proposal: dict) -> list[dict]:
    """Rejected rows matching `proposal` on every tag it states (setup, side, structure, window)."""
    keys = [k for k in MATCH_ON if proposal.get(k)]
    if not keys:
        return []
    return [
        r
        for r in reg.get("hypotheses", [])
        if r.get("status") in REJECTED and all(r.get(k) == proposal[k] for k in keys)
    ]


def rereads(reg: dict, trades_since) -> list[dict]:
    """Each rule change with its trade count since the change (`trades_since(module, arm, date)`)
    and whether its re-read is due. A change already re-read says so."""
    out = []
    for rc in reg.get("rule_changes", []):
        n = trades_since(rc["module"], rc["arm"], rc["date"])
        out.append(
            {**rc, "trades_since": n, "due": not rc.get("reread") and n is not None and n >= REREAD_AFTER}
        )
    return out


def _fill(value, year: int):
    if isinstance(value, str):
        return (
            value.replace("{year}", str(year))
            .replace("{start}", f"{year}-01-01")
            .replace("{end}", f"{year}-12-31")
            .replace("{home}", core_home.home().as_posix())
        )
    if isinstance(value, list):
        return [_fill(v, year) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, year) for k, v in value.items()}
    return value


def retest_plan(reg: dict, year: int) -> dict:
    """B14: queue rows for every re-testable hypothesis with a recipe; the rest listed as needing one.
    A recipe's strings may carry {year}, {start}, {end} and {home} (the cherrypick home, so a
    script is handed a real path)."""
    rows, missing, done, declined = [], [], [], []
    for r in reg.get("hypotheses", []):
        if r.get("status") not in RETESTABLE:
            continue
        if any(t.get("year") == year for t in r.get("retests") or []):
            done.append(r["study"])
            continue
        recipe = r.get("retest")
        if not recipe:
            # A row that says why it has none is a decision, not a gap.
            (declined if r.get("retest_note") else missing).append(r["study"])
            continue
        rows.append({
            "study": r["study"],
            "step": f"retest-{year}",
            "kind": recipe["kind"],
            "args": _fill(recipe["args"], year),
            "output": _fill(recipe.get("output"), year),
        })  # fmt: skip
    return {
        "year": year,
        "queue": rows,
        "needs_recipe": missing,
        "declined": declined,
        "already_recorded": done,
    }
