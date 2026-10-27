"""Every arm a module's example config declares carries a one-line `_summary` (2026-10-27).

The console's arm cards lead with that line (docs/progress-queue-arms-plan.md, phase 3); an arm
without one shows only its name, which is the gap the cards exist to close. Driven off each module's
own example config -- whatever registry it declares, under whichever spelling -- so a new arm is
covered the day it is added, and a new module by adding its file here. `_since` and `_retired`, where
written, must be ISO dates: the cards print them as dates.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = {
    "flies": "packages/flies/config.example.json",
    "meic": "packages/meic/config.risk.example.json",
    "calendars": "packages/calendars/config.example.json",
    "pmcc": "packages/pmcc/config.example.json",
    "curve": "packages/curve/config.example.json",
    "contango": "packages/contango/config.example.json",
    "bwb": "packages/bwb/config.example.json",
}
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def arms_of(doc: dict) -> dict[str, dict]:
    """The registry under any spelling the suite accepts (`core.config.ARM_REGISTRY_KEYS`)."""
    for key in ("arms", "books", "profiles"):
        if isinstance(doc.get(key), dict):
            return {n: b for n, b in doc[key].items() if not n.startswith("_") and isinstance(b, dict)}
    return {}


def missing_summaries(doc: dict) -> list[str]:
    return sorted(n for n, b in arms_of(doc).items() if not str(b.get("_summary") or "").strip())


def bad_dates(doc: dict) -> list[str]:
    return sorted(
        f"{n}.{k}"
        for n, b in arms_of(doc).items()
        for k in ("_since", "_retired")
        if k in b and not (isinstance(b[k], str) and ISO.match(b[k]))
    )


@pytest.mark.parametrize("module", sorted(EXAMPLES))
def test_every_example_arm_has_a_summary_and_dated_fields_are_dates(module):
    doc = json.loads((ROOT / EXAMPLES[module]).read_text(encoding="utf-8"))
    assert arms_of(doc), f"{module}: the example declares no arm registry"
    assert missing_summaries(doc) == [], f"{module}: arms with no _summary"
    assert bad_dates(doc) == [], f"{module}: _since/_retired must be YYYY-MM-DD"


def test_the_guard_fires():
    """Shown to fail: a declared arm with its summary removed, and a date that is not one."""
    books = {"control": {"_summary": "x"}, "near": {"enabled": False}, "_note": "group notes are not arms"}
    doc = {"books": books}
    assert missing_summaries(doc) == ["near"]
    assert bad_dates({"arms": {"a": {"_summary": "x", "_since": "Oct 6"}}}) == ["a._since"]
