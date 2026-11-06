"""The snapshot's time-spread reading: today's file only, a trial never, an absence said."""

import json
from datetime import date

from cherrypick.orchestrator import managesnap


def test_only_todays_reading_is_carried(tmp_path):
    (tmp_path / "2026-11-05.json").write_text(json.dumps({"session": "2026-11-05", "rows": [1]}))
    (tmp_path / "2026-11-06.trial.json").write_text(json.dumps({"session": "2026-11-06", "rows": [2]}))
    got = managesnap.time_spreads_reading(date(2026, 11, 6), tmp_path)
    assert got["error"].startswith("no time-spread reading today")
    (tmp_path / "2026-11-06.json").write_text(json.dumps({"session": "2026-11-06", "rows": [3]}))
    got = managesnap.time_spreads_reading(date(2026, 11, 6), tmp_path)
    assert got["error"] is None and got["rows"] == [3]


def test_an_unreadable_file_is_an_error_not_a_crash(tmp_path):
    (tmp_path / "2026-11-06.json").write_text("{not json")
    got = managesnap.time_spreads_reading(date(2026, 11, 6), tmp_path)
    assert "unreadable" in got["error"]
