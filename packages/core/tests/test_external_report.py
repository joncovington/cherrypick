"""The external-report contract (cherrypick.core.external_report): valid, each kind of invalid, stale
versus fresh, and what `external-check` prints."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cherrypick.core import external_report as er

NOW = datetime(2026, 11, 9, 22, 0, tzinfo=timezone.utc)
AS_OF = "2026-11-09T16:30:00-05:00"  # 21:30 UTC, half an hour before NOW


def good():
    return {
        "schema": 1,
        "id": "demo",
        "title": "Demo",
        "source": "my script",
        "as_of": AS_OF,
        "stale_after_hours": 24,
        "summary": "one line",
        "sections": [
            {
                "heading": "Table",
                "table": {"columns": ["a", "b"], "rows": [["x", 1], ["y", None], ["z", 2.5]]},
            },
            {"heading": "Notes", "text": "plain words"},
        ],
    }


def write(tmp_path, doc, name="demo"):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_a_valid_report_passes_and_is_fresh(tmp_path):
    result = er.check_file(write(tmp_path, good()), now=NOW)
    assert result.valid and not result.stale and result.problems == []
    assert 0.4 < result.age_hours < 0.6


def test_a_report_older_than_its_window_is_stale_but_valid(tmp_path):
    result = er.check_file(write(tmp_path, good()), now=NOW + timedelta(hours=25))
    assert result.valid and result.stale


def test_the_summary_is_optional(tmp_path):
    doc = good()
    del doc["summary"]
    assert er.check_file(write(tmp_path, doc), now=NOW).valid


def mutate(doc, edit):
    out = copy.deepcopy(doc)
    edit(out)
    return out


INVALID = {
    "wrong schema number": (lambda d: d.update(schema=2), "schema"),
    "missing title": (lambda d: d.pop("title"), "missing 'title'"),
    "empty title": (lambda d: d.update(title=""), "title: must not be empty"),
    "unknown field": (lambda d: d.update(extra=1), "unknown field 'extra'"),
    "id with capitals": (lambda d: d.update(id="Demo"), "id"),
    "id differs from the file name": (lambda d: d.update(id="other"), "does not match the file name"),
    "as_of without an offset": (lambda d: d.update(as_of="2026-11-09T16:30:00"), "UTC offset"),
    "as_of not a date": (lambda d: d.update(as_of="yesterday"), "as_of"),
    "zero stale window": (lambda d: d.update(stale_after_hours=0), "stale_after_hours"),
    "stale window a string": (lambda d: d.update(stale_after_hours="24"), "must be number"),
    "stale window a boolean": (lambda d: d.update(stale_after_hours=True), "must be number"),
    "summary too long": (lambda d: d.update(summary="x" * 201), "longer than 200"),
    "no sections": (lambda d: d.update(sections=[]), "at least 1"),
    "section with neither table nor text": (lambda d: d["sections"].append({"heading": "H"}), "sections[2]"),
    "section with both": (
        lambda d: d["sections"][1].update(table={"columns": ["a"], "rows": []}),
        "sections[1]",
    ),
    "short row": (lambda d: d["sections"][0]["table"]["rows"].append(["only"]), "1 cells for 2 columns"),
    "object in a cell": (lambda d: d["sections"][0]["table"]["rows"].append([{"a": 1}, 2]), "rows[3][0]"),
    "boolean in a cell": (lambda d: d["sections"][0]["table"]["rows"].append([True, 2]), "rows[3][0]"),
    "too many rows": (lambda d: d["sections"][0]["table"].update(rows=[["a", 1]] * 501), "more than 500"),
    "too many columns": (
        lambda d: d["sections"][0]["table"].update(columns=list("abcdefghijklm"), rows=[]),
        "more than 12",
    ),
    "runaway text": (lambda d: d["sections"][1].update(text="x" * 5001), "longer than 5000"),
    "too many sections": (lambda d: d.update(sections=[{"heading": "h", "text": "t"}] * 21), "more than 20"),
}


@pytest.mark.parametrize("name", sorted(INVALID))
def test_each_kind_of_invalid_report_is_named(tmp_path, name):
    edit, said = INVALID[name]
    result = er.check_file(write(tmp_path, mutate(good(), edit)), now=NOW)
    assert not result.valid
    assert any(said in p for p in result.problems), (name, result.problems)


def test_several_reasons_are_all_reported(tmp_path):
    doc = mutate(good(), lambda d: d.update(title="", stale_after_hours=-1))
    assert len(er.check_file(write(tmp_path, doc)).problems) >= 2


def test_not_json_and_not_an_object_and_missing_file_are_reasons_not_crashes(tmp_path):
    path = tmp_path / "demo.json"
    path.write_text("{oops", encoding="utf-8")
    assert "not readable JSON" in er.check_file(path).problems[0]
    path.write_text("[1, 2]", encoding="utf-8")
    assert "must be object" in er.check_file(path).problems[0]
    assert "cannot read" in er.check_file(tmp_path / "gone.json").problems[0]


def test_an_oversize_file_is_refused_before_it_is_parsed(tmp_path):
    path = tmp_path / "demo.json"
    path.write_text(json.dumps({"pad": "x" * er.MAX_BYTES}), encoding="utf-8")
    assert "byte limit" in er.check_file(path).problems[0]


def test_describe_lists_what_the_card_will_draw(tmp_path):
    lines = er.describe(er.check_file(write(tmp_path, good()), now=NOW))
    text = "\n".join(lines)
    assert "title: Demo" in text and "source: my script" in text and AS_OF in text
    assert "state: fresh" in text
    assert "section: Table (table, 3 rows)" in text and "section: Notes (text, 11 characters)" in text
    stale = er.describe(er.check_file(write(tmp_path, good()), now=NOW + timedelta(days=3)))
    assert any(line.startswith("state: STALE") for line in stale)


def test_describe_of_an_invalid_file_is_one_line_per_reason(tmp_path):
    doc = mutate(good(), lambda d: d.update(title=""))
    lines = er.describe(er.check_file(write(tmp_path, doc)))
    assert lines and all(line.startswith("invalid: ") for line in lines)


CASES = json.loads((Path(__file__).parent / "fixtures" / "external_report_cases.json").read_text("utf-8"))[
    "cases"
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_the_shared_cases_agree_with_the_console_reader(case):
    """The console server checks the same file of cases in its own test: the two validators read one
    schema, and this is what keeps them from drifting."""
    problems = er.validate_document(case["doc"], expected_id=case["stem"])
    assert (problems == []) is case["valid"], problems


def test_bar_problems_name_the_column(tmp_path):
    def with_bars(bars):
        doc = good()
        doc["sections"][0]["table"]["bars"] = bars
        return er.validate_document(doc, "demo")

    assert with_bars([1]) == []  # a number column with a null in it is fine
    assert "column 0 ('a') is not numeric" in with_bars([0])[0]
    assert "column 5 does not exist (2 columns)" in with_bars([5])[0]
    assert with_bars([-1]) and with_bars(["1"]) and with_bars([1.5])


def test_the_discord_card_and_describe_ignore_bars(tmp_path):
    doc = good()
    doc["sections"][0]["table"]["bars"] = [1]
    assert er.check_file(write(tmp_path, doc), now=NOW).valid


def test_the_schema_file_loads_and_names_every_contract_field():
    schema = er.load_schema()
    assert set(schema["required"]) == {
        "schema",
        "id",
        "title",
        "source",
        "as_of",
        "stale_after_hours",
        "sections",
    }
