"""The event-history file for item 75 (packages/backtest/scripts/event_history_item75.py): folded from the
calendar files, with what each label covers, and the approximation named."""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

from cherrypick.ensemble.events import EventCalendar

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "event_history_item75.py"


def _module():
    spec = importlib.util.spec_from_file_location("event_history_item75", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = _module()


def calendar_dir(tmp_path, *, cpi=True, umich=None):
    d = tmp_path / "calendar"
    d.mkdir()
    rel = [{"name": m.CPI_NAME, "at": "2025-01-15"}, {"name": "Employment Situation", "at": "2025-01-10"}]
    (d / "fred_history.json").write_text(
        json.dumps(
            {"releases": rel if cpi else [], "coverage": [["2025-01-01", "2025-03-31"]] if cpi else []}
        ),
        encoding="utf-8",
    )
    if umich:
        (d / "umich.json").write_text(json.dumps({"releases": umich}), encoding="utf-8")
    return d


def test_cpi_dates_cover_only_what_fred_history_speaks_for(tmp_path):
    doc = m.build(calendar_dir(tmp_path), date(2026, 11, 13))
    assert doc["data"]["CPI"] == ["2025-01-15"]
    cal = EventCalendar(doc["data"], doc["covered"])
    assert cal.occurs("CPI", date(2025, 1, 15), date(2025, 1, 15)) is True
    assert cal.occurs("CPI", date(2025, 1, 16), date(2025, 1, 16)) is False
    assert cal.occurs("CPI", date(2023, 5, 10), date(2023, 5, 10)) is None  # before the history: unknown
    (tmp_path / "x").mkdir()
    empty = m.build(calendar_dir(tmp_path / "x", cpi=False), date(2026, 11, 13))
    assert empty["covered"]["CPI"] == [] and empty["data"]["CPI"] == []


def test_sentiment_is_the_second_and_fourth_friday_unless_michigan_said_otherwise():
    plain = m.sentiment(2026, 2026, None)
    assert "2026-10-09" in plain and "2026-10-23" in plain  # 2nd and 4th Fridays of October
    told = m.sentiment(2026, 2026, {"releases": [{"at": "2026-10-16T10:00"}, {"at": "2026-10-30T10:00"}]})
    assert "2026-10-16" in told and "2026-10-09" not in told
    assert "2026-11-13" in told  # a month it knows nothing of keeps the rule


def test_the_report_names_the_approximation_and_counts_days_not_dates(tmp_path):
    doc = m.build(calendar_dir(tmp_path), date(2026, 11, 13))
    rep = m.report(doc, "2022-05-16")
    assert rep["Sentiment"]["approximate"] is True and rep["CPI"]["approximate"] is False
    assert rep["CPI"]["covered_days"] == 90 and rep["CPI"]["of"] > rep["CPI"]["covered_days"]
    assert set(rep["Sentiment"]) == {"covered_days", "of", "approximate"}
