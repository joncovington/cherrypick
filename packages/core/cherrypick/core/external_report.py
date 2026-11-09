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
from decimal import ROUND_HALF_UP, Decimal
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
    if "$ref" in schema:
        schema = load_schema()["$defs"][schema["$ref"].rsplit("/", 1)[-1]]
    if "const" in schema and value != schema["const"]:
        return [f"{here}: must be {schema['const']!r}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{here}: must be one of {', '.join(map(str, schema['enum']))}"]
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
            # Report the branch the value came nearest to: among the branches whose type fits the
            # value (a cell that is an object is judged as an object, not as a string), the one with
            # the fewest reasons.
            fits = [
                t
                for branch, t in zip(schema["oneOf"], tried, strict=True)
                if "type" not in branch
                or any(
                    _TYPES[k](value)
                    for k in ([branch["type"]] if isinstance(branch["type"], str) else branch["type"])
                )
            ]
            problems += min(fits or tried, key=len) or [f"{here}: matches more than one shape"]
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
        problems += _table_problems(f"sections[{i}].table", table)
    return problems


# ------------------------------------------------------------------------------ presentation hints
NUMERIC_FORMATS = ("money", "compact", "int", "percent", "signed", "decimal")
# A percent column's numbers are percents (56 is 56%), so a value past this is a unit mistake.
PERCENT_LIMIT = 1000
MINUS = "−"


def column_spec(column: Any) -> dict[str, Any]:
    """A column (a string or an object) as one dict with every hint defaulted."""
    spec = {"label": column} if isinstance(column, str) else dict(column)
    spec.setdefault("format", "text")
    spec.setdefault("tone", "none")
    spec.setdefault("stack", None)
    spec.setdefault("bar", None)
    spec.setdefault("align", "left" if spec["format"] == "text" and not spec["stack"] else "right")
    return spec


def cell_parts(cell: Any) -> dict[str, Any]:
    """A cell (scalar, array or object) as {value, text, tone, suffix, marker}, absent parts None."""
    if isinstance(cell, dict):
        return {k: cell.get(k) for k in ("value", "text", "tone", "suffix", "marker")}
    return {"value": cell, "text": None, "tone": None, "suffix": None, "marker": None}


def row_cells(row: Any) -> list[Any]:
    return row["cells"] if isinstance(row, dict) else row


def _fixed(value: float, places: int) -> str:
    """Grouped and rounded half up on the exact value, as JavaScript's toFixed does, so the console
    and this module print the same text."""
    step = Decimal(1).scaleb(-places)
    return f"{Decimal(value).quantize(step, rounding=ROUND_HALF_UP):,.{places}f}"


def format_value(fmt: str, value: Any) -> str:
    """`value` as the column's `fmt` shows it: a dash for null, a string as written. Money is whole
    dollars (24_040_000 is $24.04M); percent takes the percent number itself (56 is 56%)."""
    if value is None:
        return "—"
    if not _is_number(value):
        return str(value)
    mag = abs(value)
    sign = MINUS if value < 0 else ""
    if fmt == "money":
        if mag >= 1e9:
            body = _fixed(mag / 1e9, 2) + "B"
        elif mag >= 1e6:
            body = _fixed(mag / 1e6, 2) + "M"
        elif mag >= 1e5:
            body = _fixed(mag / 1e3, 0) + "K"
        elif mag >= 1e3:
            body = _fixed(mag / 1e3, 1) + "K"
        else:
            body = _fixed(mag, 2)
        return f"{sign}${body}"
    if fmt == "compact":
        if mag >= 1e9:
            return f"{sign}{_fixed(mag / 1e9, 1)}B"
        if mag >= 1e6:
            return f"{sign}{_fixed(mag / 1e6, 1)}M"
        if mag >= 1e3:
            return f"{sign}{_fixed(mag / 1e3, 1)}K"
        return f"{sign}{_fixed(mag, 0)}"
    if fmt == "int":
        return f"{sign}{_fixed(mag, 0)}"
    if fmt == "percent":
        whole = _fixed(mag, 1).rstrip("0").rstrip(".")
        return f"{sign}{whole}%"
    if fmt == "signed":
        rounded = _fixed(mag, 0)
        return "0" if rounded == "0" else f"{'+' if value > 0 else MINUS}{rounded}"
    shown = _fixed(mag, 6).rstrip("0").rstrip(".")
    return f"{sign}{shown}"


def cell_text(column: Any, cell: Any) -> str:
    """What one cell reads as plain text: its `text` if it has one, else its value formatted by the
    column, then its suffix. A stacked cell reads as its numbers joined by a slash."""
    if isinstance(cell, list):
        return "/".join(format_value("compact", n) for n in cell)
    parts = cell_parts(cell)
    text = (
        parts["text"]
        if parts["text"] is not None
        else format_value(column_spec(column)["format"], parts["value"])
    )
    suffix = parts["suffix"]
    return f"{text} {suffix['text']}" if suffix and suffix["text"] else text


def _table_problems(where: str, table: dict[str, Any]) -> list[str]:
    cols = [column_spec(c) for c in table["columns"]]
    width = len(cols)
    rows = table["rows"]
    for r, row in enumerate(rows):
        if len(row_cells(row)) != width:
            return [f"{where}.rows[{r}]: {len(row_cells(row))} cells for {width} columns"]
    problems: list[str] = []
    for c, col in enumerate(cols):
        name = f"{where}.columns[{c}] ({col['label']!r})"
        stack = col["stack"]
        if col["tone"] == "sign" and col["format"] not in NUMERIC_FORMATS:
            problems.append(f"{name}: tone 'sign' needs a numeric format")
        if stack and col["format"] != "text":
            problems.append(f"{name}: a stacked column takes no format")
        for r, row in enumerate(rows):
            cell = row_cells(row)[c]
            at = f"{where}.rows[{r}][{c}]"
            value = cell if isinstance(cell, list) else cell_parts(cell)["value"]
            if stack:
                if cell is None or (isinstance(cell, dict) and value is None):
                    continue
                if not isinstance(value, list) or len(value) != len(stack):
                    problems.append(f"{at}: a stacked cell is {len(stack)} numbers, one per segment")
                elif any(isinstance(n, (int, float)) and n < 0 for n in value):
                    problems.append(f"{at}: a stacked segment cannot be negative")
                continue
            if isinstance(value, list):
                problems.append(f"{at}: an array is only for a stacked column")
            elif col["format"] == "percent" and _is_number(value) and abs(value) > PERCENT_LIMIT:
                problems.append(f"{at}: {value} is over {PERCENT_LIMIT} (a percent column takes 56 for 56%)")
    for col in table.get("bars", []):
        at = f"{where}.bars"
        if col >= width:
            problems.append(f"{at}: column {col} does not exist ({width} columns)")
        else:
            problems += _bar_problems(at, col, cols[col], rows, "column")
    for c, col in enumerate(cols):
        bar = col.get("bar")
        if bar is None:
            continue
        name = f"{where}.columns[{c}].bar"
        scale = bar.get("scale")
        if scale is not None and not scale[0] < scale[1]:
            problems.append(f"{name}.scale: min {scale[0]} must be below max {scale[1]}")
        problems += _bar_problems(name, c, col, rows, "column")
    return problems


def _bar_problems(at: str, c: int, col: dict[str, Any], rows: list[Any], word: str) -> list[str]:
    if col["stack"]:
        return [f"{at}: {word} {c} ({col['label']!r}) is stacked already"]
    if any(isinstance(cell_parts(row_cells(row)[c])["value"], str) for row in rows):
        return [f"{at}: {word} {c} ({col['label']!r}) is not numeric"]
    return []


def hints_used(doc: dict[str, Any]) -> list[str]:
    """A short phrase per kind of presentation hint the report uses, for `describe`."""
    columns = formatted = stacks = marked = toned = 0
    numbered = keyed = notes = emphasised = 0
    for section in doc["sections"]:
        table = section.get("table")
        if table is None:
            continue
        numbered += bool(section.get("numbered"))
        keyed += len(section.get("key", []))
        notes += bool(section.get("footnote"))
        for col in table["columns"]:
            if isinstance(col, dict):
                columns += 1
                formatted += col.get("format", "text") != "text" or col.get("tone") == "sign"
                stacks += bool(col.get("stack"))
        for row in table["rows"]:
            emphasised += isinstance(row, dict) and "emphasis" in row
            for cell in row_cells(row):
                if isinstance(cell, dict):
                    toned += cell.get("tone") is not None or cell.get("suffix") is not None
                    marked += cell.get("marker") is not None
    bars = sum(len(s["table"].get("bars", [])) for s in doc["sections"] if "table" in s)
    bars += sum(
        isinstance(c, dict) and "bar" in c
        for s in doc["sections"]
        if "table" in s
        for c in s["table"]["columns"]
    )
    out = [
        (formatted, f"{formatted} column{'' if formatted == 1 else 's'} formatted"),
        (stacks, f"{stacks} stacked bar{'' if stacks == 1 else 's'}"),
        (bars, f"{bars} bar column{'' if bars == 1 else 's'}"),
        (toned, "toned cells"),
        (marked, "markers"),
        (emphasised, f"{emphasised} emphasised row{'' if emphasised == 1 else 's'}"),
        (numbered, "numbered"),
        (keyed, f"key of {keyed}"),
        (notes, "footnote"),
    ]
    return [text for n, text in out if n]


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
    hints = hints_used(doc)
    if hints:
        lines.append(f"hints: {', '.join(hints)}")
    return lines
