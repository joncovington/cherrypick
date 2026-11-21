"""Every declared idea and setting, with its one held-out test or none yet: `read declared`.

The idea search (`ideafiles`) and the settings grid (`gridfiles`) each let a person declare a
surviving item, which drafts its register row into the result, and then spend the run's held-out
sessions on it once. This lists every declared item across every automation's results, for
`core.changes`, which reads one waiting on its test as `waiting` and one tested as `closed` with its
verdict. An item carries its title and measure from its drafted row (no figures), and its held-out
read when it has one (the verdict and the interval). A result over the vendor's store is `licensed`,
and so is everything read from it: the console withholds it while licensed results are hidden.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import paths

#: Result file prefix, its kind, and the key a declared item names itself by.
KINDS = (("ideas-", "idea", "idea"), ("grid-", "setting", "setting"))


def _docs() -> list[tuple[str, str, dict, bool]]:
    out = []
    for licensed in (False, True):
        d = paths.automations_dir(licensed)
        if not d.is_dir():
            continue
        for prefix, kind, key in KINDS:
            for p in sorted(d.glob(f"{prefix}*.json")):
                try:
                    doc = json.loads(Path(p).read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(doc, dict) and doc.get("declared"):
                    out.append((kind, key, doc, bool(doc.get("licensed")) or licensed))
    return out


def items(docs: list[tuple[str, str, dict, bool]], *, automation: str | None = None) -> list[dict[str, Any]]:
    """Each declared item of `docs` (pure over the parsed results), newest declaration first."""
    out = []
    for kind, key, doc, licensed in docs:
        if automation is not None and doc.get("automation") != automation:
            continue
        uses = {u.get(key): u for u in doc.get("held_out_uses") or []}
        for d in doc.get("declared") or []:
            item = d.get(key)
            row = d.get("row") or {}
            use = uses.get(item)
            out.append(
                {
                    "source_kind": kind,
                    "automation": doc.get("automation"),
                    "result": doc.get("name"),
                    "run": doc.get("run"),
                    "item": item,
                    "title": row.get("title"),
                    "measure": row.get("measure"),
                    "declared_at": d.get("at"),
                    "licensed": licensed,
                    "held_out": None
                    if use is None
                    else {
                        "at": use.get("at"),
                        "verdict": use.get("verdict"),
                        "ci": use.get("ci"),
                        "use": use.get("use"),
                    },
                }
            )
    return sorted(out, key=lambda r: str(r["declared_at"] or ""), reverse=True)


def declared(automation: str | None = None) -> dict[str, Any]:
    """`read declared`: every declared item on this machine, optionally one automation's."""
    return {"ok": True, "items": items(_docs(), automation=automation)}
