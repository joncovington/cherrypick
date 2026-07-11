"""Unit tests for orchestrator.util.first_json.

Guards the exact class of bug seen in Stage 0: streamer.py --status prints a JSON status line
followed by extra log output, which a plain json.loads rejects with "Extra data".
"""

import pytest

from orchestrator.util import first_json

pytestmark = pytest.mark.unit


def test_clean_json_object():
    assert first_json('{"running": true, "pid": 123}') == {"running": True, "pid": 123}


def test_json_with_trailing_extra_data():
    # The streamer-status shape: valid JSON on line 1, extra output after.
    out = '{"running": true, "pid": 999}\nINFO: streamer heartbeat ok\n'
    assert first_json(out) == {"running": True, "pid": 999}


def test_json_not_on_first_line():
    out = "starting up...\n{\"ok\": true}\n"
    assert first_json(out) == {"ok": True}


def test_empty_and_none():
    assert first_json("") == {}
    assert first_json(None) == {}


def test_no_json_present():
    assert first_json("plain text, no json here") == {}


def test_top_level_non_dict_is_ignored():
    # A JSON array is valid JSON but not a status object.
    assert first_json("[1, 2, 3]") == {}
