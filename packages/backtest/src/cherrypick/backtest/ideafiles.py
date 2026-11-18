"""The idea search's files (#1294): which run a search reads, its results, and the two writes a person
makes from one, Declare and the one held-out test.

- **Plan** (`plan`): the newest Builder run of the automation long enough to search (`MIN_SESSIONS`
  sessions with an entry), its span and the split `ideasearch.split_date` fixes for it. The orchestrator
  asks this when it queues a search, so the split is in the queued command before the search starts.
- **Results** (`ideas-<automation>-<YYMMDDHHMM>.json`) sit beside the run they read
  (`paths.automations_dir`): under `results/` when that run read the vendor's store (licensed, purged
  with it), beside it otherwise. Never into the repo.
- **Declare** (`declare`) writes a surviving, testable idea's register row draft into its result, the
  split already fixed. The row is added to `research/hypotheses.json` by hand, with its mechanism.
- **The held-out test** (`holdout`) reads the run's later sessions for one declared idea, once: a
  second test of the same idea is refused, and every use is logged on the result (`held_out_uses`),
  so a reader sees how many ideas have spent that part.

A licensed run is read for new work only while the licence allows it (`licensing.status`).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from cherrypick.core import home, jsonio
from cherrypick.core.clock import ET

from . import ideasearch, licensing, paths

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")
RESULT = "ideas-{name}-"
#: Fewest sessions with an entry a run needs before it is worth searching.
MIN_SESSIONS = 120


def _err(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def _builder_docs(name: str):
    from .readout import _builder_docs as docs

    return docs(name)


def _runs_by_name(name: str) -> dict[str, tuple[dict, bool]]:
    docs, _ = _builder_docs(name)
    return {d["name"]: (d, lic) for d, lic in docs}


def _entry_sessions(doc: dict) -> list[str]:
    return sorted({str(t.get("session") or "")[:10] for t in doc.get("trades") or [] if t.get("session")})


def plan(name: str) -> dict[str, Any]:
    """The run a search of automation `name` would read, and the split fixed for it."""
    if not NAME.match(name or ""):
        return _err("name must be an automation's name")
    docs, _ = _builder_docs(name)
    for doc, licensed in reversed(docs):
        sessions = _entry_sessions(doc)
        if len(sessions) >= MIN_SESSIONS:
            span = doc.get("sessions") or [sessions[0], sessions[-1]]
            return {
                "ok": True,
                "name": name,
                "run": doc["name"],
                "licensed": licensed,
                "sessions": span,
                "entry_sessions": len(sessions),
                "split": ideasearch.split_date(span[0], span[-1]),
            }
    return _err(
        f"no Builder run of {name} has {MIN_SESSIONS} sessions with an entry yet; "
        "run a longer window first (everything held)"
    )


def result_path(name: str, run_name: str, licensed: bool) -> Path:
    return paths.automations_dir(licensed) / f"{run_name}.json"


def home_path(p: Path) -> str:
    """A result's path as the register writes it, `{home}/...`, or as it is when outside the home."""
    try:
        return "{home}/" + p.resolve().relative_to(home.home().resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def _results(name: str) -> list[tuple[Path, dict, bool]]:
    pattern = re.compile(re.escape(RESULT.format(name=name)) + r"\d{10}\.json$")
    out = []
    for licensed in (False, True):
        d = paths.automations_dir(licensed)
        if not d.is_dir():
            continue
        for p in sorted(d.glob("ideas-*.json")):
            if not pattern.fullmatch(p.name):
                continue
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(doc, dict) and isinstance(doc.get("name"), str):
                out.append((p, doc, bool(doc.get("licensed")) or licensed))
    out.sort(key=lambda r: str(r[1].get("generated_at") or ""))
    return out


def _find(name: str, rid: str) -> tuple[Path, dict, bool] | None:
    return next((r for r in _results(name) if r[1].get("name") == rid), None)


BRIEF = (
    "name", "automation", "run", "symbol", "source", "sessions", "split", "base", "tried", "survivors",
    "holm_survivors", "bh_survivors", "generated_at",
)  # fmt: skip
FULL = ("draws", "null_min_median", "rules", "menu")


def _view(doc: dict, licensed: bool, *, rows: bool) -> dict[str, Any]:
    declared = {d.get("idea"): d for d in doc.get("declared") or []}
    uses = doc.get("held_out_uses") or []
    out: dict[str, Any] = {k: doc.get(k) for k in BRIEF}
    out.update(licensed=licensed, declared=list(declared), held_out_used=len(uses))
    if rows:
        by_idea = {u.get("idea"): u for u in uses}
        out.update({k: doc.get(k) for k in FULL})
        out["rows"] = [
            {
                **r,
                "declaration": (declared.get(r["id"]) or {}).get("row"),
                "held_out_read": by_idea.get(r["id"]),
            }
            for r in doc.get("rows") or []
        ]
        out["held_out_uses"] = uses
    return out


def searches(name: str) -> dict[str, Any]:
    """Every idea search of automation `name`, newest first, in brief (no rows)."""
    if not NAME.match(name or ""):
        return _err("name must be an automation's name")
    return {
        "ok": True,
        "name": name,
        "searches": [_view(d, lic, rows=False) for _, d, lic in reversed(_results(name))],
    }


def search_result(name: str, rid: str) -> dict[str, Any]:
    """One search's whole result: its rows, what has been declared and how the held-out part was spent."""
    found = _find(name, rid) if NAME.match(name or "") else None
    if found is None:
        return _err(f"no idea search {rid!r} for {name}")
    _, doc, licensed = found
    return {"ok": True, **_view(doc, licensed, rows=True)}


def declare(name: str, rid: str, idea: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Draft the register row of one surviving idea that the held-out part can test, into its result."""
    found = _find(name, rid) if NAME.match(name or "") else None
    if found is None:
        return _err(f"no idea search {rid!r} for {name}")
    path, doc, _licensed = found
    row = next((r for r in doc.get("rows") or [] if r.get("id") == idea), None)
    if row is None:
        return _err(f"no idea {idea!r} in {rid}")
    if not row.get("survives"):
        return _err("only an idea that survived every check can be declared")
    if not (row.get("held_out") or {}).get("testable"):
        return _err("the held-out part cannot detect this idea's effect: not testable on our history")
    declared = doc.setdefault("declared", [])
    if any(d.get("idea") == idea for d in declared):
        return _err("this idea is declared already")
    draft = ideasearch.declaration(doc, idea, result_path=home_path(path))
    stamp = (now or datetime.now(ET)).isoformat(timespec="seconds")
    declared.append({"idea": idea, "at": stamp, "row": draft})
    jsonio.write_json_atomic(path, doc)
    return {"ok": True, "name": name, "id": rid, "idea": idea, "declared_at": stamp, "row": draft}


def holdout(name: str, rid: str, idea: str, settings: dict, *, now: datetime | None = None) -> dict[str, Any]:
    """The one held-out test of a declared idea, logged on its result. Refused a second time."""
    found = _find(name, rid) if NAME.match(name or "") else None
    if found is None:
        return _err(f"no idea search {rid!r} for {name}")
    path, doc, licensed = found
    if not any(d.get("idea") == idea for d in doc.get("declared") or []):
        return _err("declare the idea before its held-out test")
    uses = doc.setdefault("held_out_uses", [])
    if any(u.get("idea") == idea for u in uses):
        return _err("this idea has had its one held-out test")
    if licensed and not licensing.status(settings)["active"]:
        return _err("the licence does not allow new work on this licensed run")
    run = _runs_by_name(name).get(str(doc.get("run")))
    if run is None:
        return _err(f"the run this search read ({doc.get('run')}) is gone")
    read = ideasearch.holdout(run[0].get("trades") or [], doc.get("features") or {}, doc["split"], idea)
    stamp = (now or datetime.now(ET)).isoformat(timespec="seconds")
    uses.append({"idea": idea, "at": stamp, "use": len(uses) + 1, **read})
    jsonio.write_json_atomic(path, doc)
    return {"ok": True, "name": name, "id": rid, "idea": idea, "tested_at": stamp, "uses": len(uses), **read}
