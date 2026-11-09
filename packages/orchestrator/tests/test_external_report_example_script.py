"""The example external-report script (`scripts/examples/external_report_example.py`): its shaping
function on a small inline CSV, and that what it shapes passes the contract. No network."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cherrypick.core import external_report as er

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "examples" / "external_report_example.py"

CSV_TEXT = (
    "DATE,OPEN,HIGH,LOW,CLOSE\n"
    "11/03/2026,17.0,18.0,16.5,17.5\n"
    "11/04/2026,17.5,19.0,17.0,18.5\n"
    "11/05/2026,18.5,18.9,16.0,16.5\n"
    "not a date,1,2,3,4\n"
    "11/06/2026,16.5,17.0,15.5,16.0\n"
)
NOW = datetime(2026, 11, 7, 18, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("external_report_example", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["external_report_example"] = module
    spec.loader.exec_module(module)
    return module


def test_it_shapes_the_last_sessions_newest_first_with_the_change(example):
    doc = example.shape_report(CSV_TEXT, NOW, sessions=3)
    rows = doc["sections"][0]["table"]["rows"]
    assert [r[0] for r in rows] == ["2026-11-06", "2026-11-05", "2026-11-04"]
    assert rows[0] == ["2026-11-06", 16.5, 17.0, 15.5, 16.0, -0.5]
    assert rows[2][-1] == 1.0  # 18.5 less the 17.5 before it, which is outside the three shown
    assert "16.00" in doc["summary"] and "2026-11-06" in doc["summary"]


def test_the_first_session_has_no_change(example):
    rows = example.shape_report(CSV_TEXT, NOW, sessions=10)["sections"][0]["table"]["rows"]
    assert rows[-1][0] == "2026-11-03" and rows[-1][-1] is None


def test_what_it_shapes_passes_the_contract(example, tmp_path):
    path = tmp_path / "vix-history.json"
    path.write_text(json.dumps(example.shape_report(CSV_TEXT, NOW)), encoding="utf-8")
    result = er.check_file(path, now=NOW)
    assert result.valid, result.problems
    assert not result.stale


def test_a_file_in_the_wrong_shape_or_empty_is_an_error_not_a_report(example):
    with pytest.raises(ValueError):
        example.shape_report("<html>blocked</html>", NOW)
    with pytest.raises(ValueError):
        example.shape_report("DATE,OPEN,HIGH,LOW,CLOSE\n", NOW)


def test_the_report_lands_in_the_suite_homes_external_folder(example, tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    assert example.report_path() == tmp_path / "data" / "external" / "vix-history.json"
