"""The settings grid's files (#1294): its results, and the two writes a person makes from one, Declare
and the one held-out test. Which run a grid replays, and its split, are the idea search's plan
(`ideafiles.plan`): the newest Builder run long enough, the split two thirds through its span.

- **Results** (`grid-<automation>-<YYMMDDHHMM>.json`) sit beside the run they replayed
  (`paths.automations_dir`): licensed when that run or its store was. They hold every setting's trades
  over the whole span; a read here serves the rows and never the trades.
- **Declare** (`declare`) drafts a surviving, testable setting's register row into its result, the split
  already fixed. The row is added to `research/hypotheses.json` by hand, with its mechanism.
- **The held-out test** (`holdout`) reads the stored trades of the base and one declared setting on the
  sessions from the split, once: a second test of the same setting is refused, and every use is logged
  on the result (`held_out_uses`).

A licensed result is read for new work only while the licence allows it (`licensing.status`).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from cherrypick.core import jsonio
from cherrypick.core.clock import ET

from . import exitgrid, ideafiles, licensing, paths

NAME = ideafiles.NAME
RESULT = "grid-{name}-"


def _err(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def _results(name: str) -> list[tuple[Path, dict, bool]]:
    pattern = re.compile(re.escape(RESULT.format(name=name)) + r"\d{10}\.json$")
    out = []
    for licensed in (False, True):
        d = paths.automations_dir(licensed)
        if not d.is_dir():
            continue
        for p in sorted(d.glob("grid-*.json")):
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
    if not NAME.match(name or ""):
        return None
    return next((r for r in _results(name) if r[1].get("name") == rid), None)


BRIEF = (
    "name", "automation", "run", "symbol", "source", "sessions", "split", "base", "tried", "survivors",
    "holm_survivors", "bh_survivors", "base_matches_run", "generated_at",
)  # fmt: skip
FULL = ("draws", "null_max_median", "rules", "grid", "refused")


def _view(doc: dict, licensed: bool, *, rows: bool) -> dict[str, Any]:
    declared = {d.get("setting"): d for d in doc.get("declared") or []}
    uses = doc.get("held_out_uses") or []
    out: dict[str, Any] = {k: doc.get(k) for k in BRIEF}
    out.update(licensed=licensed, declared=list(declared), held_out_used=len(uses))
    if rows:
        by_setting = {u.get("setting"): u for u in uses}
        out.update({k: doc.get(k) for k in FULL})
        out["rows"] = [
            {
                **r,
                "declaration": (declared.get(r["id"]) or {}).get("row"),
                "held_out_read": by_setting.get(r["id"]),
            }
            for r in doc.get("rows") or []
        ]
        out["held_out_uses"] = uses
    return out


def grids(name: str) -> dict[str, Any]:
    """Every settings grid of automation `name`, newest first, in brief (no rows)."""
    if not NAME.match(name or ""):
        return _err("name must be an automation's name")
    return {
        "ok": True,
        "name": name,
        "grids": [_view(d, lic, rows=False) for _, d, lic in reversed(_results(name))],
    }


def grid_result(name: str, rid: str) -> dict[str, Any]:
    """One grid's rows, what has been declared and how the held-out part was spent. Never its trades."""
    found = _find(name, rid)
    if found is None:
        return _err(f"no settings grid {rid!r} for {name}")
    _, doc, licensed = found
    return {"ok": True, **_view(doc, licensed, rows=True)}


def declare(name: str, rid: str, setting: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Draft the register row of one surviving setting that the held-out part can test, into its result."""
    found = _find(name, rid)
    if found is None:
        return _err(f"no settings grid {rid!r} for {name}")
    path, doc, _licensed = found
    row = next((r for r in doc.get("rows") or [] if r.get("id") == setting), None)
    if row is None:
        return _err(f"no setting {setting!r} in {rid}")
    if not row.get("survives"):
        return _err("only a setting that survived every check can be declared")
    if not (row.get("held_out") or {}).get("testable"):
        return _err("the held-out part cannot detect this setting's effect: not testable on our history")
    declared = doc.setdefault("declared", [])
    if any(d.get("setting") == setting for d in declared):
        return _err("this setting is declared already")
    draft = exitgrid.declaration(doc, setting, result_path=ideafiles.home_path(path))
    stamp = (now or datetime.now(ET)).isoformat(timespec="seconds")
    declared.append({"setting": setting, "at": stamp, "row": draft})
    jsonio.write_json_atomic(path, doc)
    return {"ok": True, "name": name, "id": rid, "setting": setting, "declared_at": stamp, "row": draft}


def holdout(
    name: str, rid: str, setting: str, settings: dict, *, now: datetime | None = None
) -> dict[str, Any]:
    """The one held-out test of a declared setting, logged on its result. Refused a second time."""
    found = _find(name, rid)
    if found is None:
        return _err(f"no settings grid {rid!r} for {name}")
    path, doc, licensed = found
    if not any(d.get("setting") == setting for d in doc.get("declared") or []):
        return _err("declare the setting before its held-out test")
    uses = doc.setdefault("held_out_uses", [])
    if any(u.get("setting") == setting for u in uses):
        return _err("this setting has had its one held-out test")
    if licensed and not licensing.status(settings)["active"]:
        return _err("the licence does not allow new work on this licensed result")
    trades = doc.get("trades") or {}
    if setting not in trades or "base" not in trades:
        return _err(f"the result holds no trades for {setting!r}")
    read = exitgrid.holdout(trades["base"], trades[setting], doc.get("replayed_sessions") or [], doc["split"])
    stamp = (now or datetime.now(ET)).isoformat(timespec="seconds")
    uses.append({"setting": setting, "at": stamp, "use": len(uses) + 1, **read})
    jsonio.write_json_atomic(path, doc)
    return {
        "ok": True,
        "name": name,
        "id": rid,
        "setting": setting,
        "tested_at": stamp,
        "uses": len(uses),
        **read,
    }
