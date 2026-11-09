"""External reports: the contract a person's own script writes and the suite reads.

A report is one JSON file, ``<home>/data/external/<id>.json``. The contract is
``schemas/external_report.schema.json`` -- the single source of truth, read by this module and by the
console server alike. This module is stdlib only: it interprets the small subset of JSON Schema the
file uses (types, const, pattern, length and size bounds, required/additionalProperties, oneOf) rather
than adding a dependency, and adds the two checks a schema cannot express (the id matches the file's
name; every table row is as wide as its columns).

Pure functions over data already in hand: no network, no clock unless one is passed in.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "external_report.schema.json"
# The whole file, in bytes: past this a runaway script is refused before it is parsed.
MAX_BYTES = 512 * 1024

_AS_OF = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$")


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def parse_as_of(value: Any) -> datetime | None:
    """`value` as an aware datetime, or None unless it is ISO 8601 with a UTC offset."""
    if not isinstance(value, str) or not _AS_OF.match(value):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


_TYPES = {
    "string": lambda v: isinstance(v, str),
    "number": _is_number,
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def _kind(value: Any) -> str:
    for name in ("null", "boolean", "string", "number", "array", "object"):
        if _TYPES[name](value):
            return name
    return type(value).__name__


def _check(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    """Why `value` breaks `schema`, each reason led by the path to the offending part."""
    here = path or "report"
    if "const" in schema and value != schema["const"]:
        return [f"{here}: must be {schema['const']!r}"]
    if "type" in schema:
        wanted = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPES[t](value) for t in wanted):
            return [f"{here}: must be {' or '.join(wanted)}, not {_kind(value)}"]
    problems: list[str] = []
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            problems.append(
                f"{here}: must not be empty" if schema["minLength"] == 1 else f"{here}: too short"
            )
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            problems.append(f"{here}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            problems.append(f"{here}: does not match {schema['pattern']}")
        if schema.get("format") == "date-time-offset" and parse_as_of(value) is None:
            problems.append(
                f"{here}: must be ISO 8601 with a UTC offset (for example 2026-11-09T16:30:00-05:00)"
            )
    elif _is_number(value):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"{here}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"{here}: above {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            problems.append(f"{here}: must be above {schema['exclusiveMinimum']}")
    elif isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            problems.append(f"{here}: needs at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            problems.append(f"{here}: more than {schema['maxItems']} items ({len(value)})")
        if "items" in schema and len(value) <= schema.get("maxItems", len(value)):
            for i, item in enumerate(value):
                problems += _check(item, schema["items"], f"{path}[{i}]")
    elif isinstance(value, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                problems.append(f"{here}: missing {name!r}")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in props:
                    problems.append(f"{here}: unknown field {name!r}")
        for name, sub in props.items():
            if name in value:
                problems += _check(value[name], sub, f"{path}.{name}" if path else name)
    if "oneOf" in schema:
        tried = [_check(value, branch, path) for branch in schema["oneOf"]]
        if sum(1 for t in tried if not t) != 1:
            # Report the branch the value came nearest to: the one with the fewest reasons.
            problems += min(tried, key=len) or [f"{here}: matches more than one shape"]
    return problems


def validate_document(doc: Any, expected_id: str | None = None) -> list[str]:
    """Every reason `doc` is not a valid report; empty when it is."""
    problems = _check(doc, load_schema(), "")
    if problems or not isinstance(doc, dict):
        return problems
    if expected_id is not None and doc.get("id") != expected_id:
        problems.append(f"id: {doc.get('id')!r} does not match the file name ({expected_id!r})")
    for i, section in enumerate(doc["sections"]):
        table = section.get("table")
        if table is None:
            continue
        width = len(table["columns"])
        for r, row in enumerate(table["rows"]):
            if len(row) != width:
                problems.append(f"sections[{i}].table.rows[{r}]: {len(row)} cells for {width} columns")
                break
        else:
            for col in table.get("bars", []):
                where = f"sections[{i}].table.bars"
                if col >= width:
                    problems.append(f"{where}: column {col} does not exist ({width} columns)")
                elif any(isinstance(row[col], str) for row in table["rows"]):
                    problems.append(f"{where}: column {col} ({table['columns'][col]!r}) is not numeric")
    return problems


@dataclass
class CheckResult:
    path: Path
    problems: list[str] = field(default_factory=list)
    doc: dict[str, Any] | None = None
    stale: bool = False
    age_hours: float | None = None

    @property
    def valid(self) -> bool:
        return not self.problems and self.doc is not None


def check_file(path: str | Path, now: datetime | None = None) -> CheckResult:
    """Read and judge one report file. Never raises on bad content: every failure is a reason."""
    path = Path(path)
    out = CheckResult(path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        out.problems.append(f"cannot read the file: {exc.strerror or exc}")
        return out
    if size > MAX_BYTES:
        out.problems.append(f"file is {size} bytes, over the {MAX_BYTES} byte limit")
        return out
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        out.problems.append(f"not readable JSON: {exc}")
        return out
    out.problems = validate_document(doc, expected_id=path.stem)
    if out.problems:
        return out
    out.doc = doc
    as_of = parse_as_of(doc["as_of"])
    assert as_of is not None  # the schema's format check passed
    out.age_hours = ((now or datetime.now(timezone.utc)) - as_of).total_seconds() / 3600
    out.stale = out.age_hours > doc["stale_after_hours"]
    return out


def describe(result: CheckResult) -> list[str]:
    """What the console card will draw, or every reason the file is invalid, one line each."""
    if not result.valid or result.doc is None:
        return [f"invalid: {reason}" for reason in result.problems]
    doc = result.doc
    age = result.age_hours or 0.0
    lines = [
        f"title: {doc['title']}",
        f"source: {doc['source']}",
        f"as of: {doc['as_of']} ({age:.1f} h ago)",
        f"state: {'STALE' if result.stale else 'fresh'} (stale after {doc['stale_after_hours']} h)",
    ]
    if doc.get("summary"):
        lines.append(f"summary: {doc['summary']}")
    for section in doc["sections"]:
        if "table" in section:
            n = len(section["table"]["rows"])
            lines.append(f"section: {section['heading']} (table, {n} row{'' if n == 1 else 's'})")
        else:
            lines.append(f"section: {section['heading']} (text, {len(section['text'])} characters)")
    return lines
