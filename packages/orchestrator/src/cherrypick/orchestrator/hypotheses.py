"""`cherrypick hypotheses` — the hypothesis register and its yearly re-test (B7, B14).

`docs/hypotheses.json` holds one row per hypothesis: its four parts (what is measured, the expected
outcome as a number, the window and sample, the pass-or-fail rule), the mechanism, the rival
explanations with the check that rules each out, its status and a pointer to the plan section.
Never figures: licensed results stay under `~/.cherrypick`, so the register can live in the repo.

- `check`: every row is well formed, and every pointer names a heading that exists. Rows from item 18
  on must fill every field; legacy rows (1-17) point to the prose that holds theirs.
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

STATUSES = ("observation", "declared", "running", "passed", "failed", "no_verdict")
REJECTED = ("failed",)
RETESTABLE = ("declared", "passed", "failed", "no_verdict")
MATCH_ON = ("setup", "side", "structure", "window")
FOUR_PARTS = ("measure", "expected", "sample", "rule")
REQUIRED_NEW = (*FOUR_PARTS, "mechanism", *MATCH_ON)
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
    rows, missing, done = [], [], []
    for r in reg.get("hypotheses", []):
        if r.get("status") not in RETESTABLE:
            continue
        if any(t.get("year") == year for t in r.get("retests") or []):
            done.append(r["study"])
            continue
        recipe = r.get("retest")
        if not recipe:
            missing.append(r["study"])
            continue
        rows.append({
            "study": r["study"],
            "step": f"retest-{year}",
            "kind": recipe["kind"],
            "args": _fill(recipe["args"], year),
            "output": _fill(recipe.get("output"), year),
        })  # fmt: skip
    return {"year": year, "queue": rows, "needs_recipe": missing, "already_recorded": done}
