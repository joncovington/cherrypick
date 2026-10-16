"""The collector's security-list capture: which response it is, which session it describes, and that
a short or unlabelled list is set aside rather than kept. Synthetic rows only; no vendor data."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_vendor_edition.py"


def _module():
    spec = importlib.util.spec_from_file_location("fetch_vendor_edition", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows(n, labelled=True):
    return [
        {
            "symbol": f"S{i}",
            "type": "Stock",
            "directionalEdge": "sb" if labelled else None,
            "sentiment1M": 4,
            "sentiment6M": 4,
            "technicalRank": 9,
            "marketCap": 1e9,  # not kept
        }
        for i in range(n)
    ]


def test_only_the_list_itself_is_the_security_list():
    m = _module()
    assert m.is_securities("https://x/api/optionsplay/api/v1/securities")
    assert m.is_securities("https://x/api/optionsplay/api/v1/securities?region=US")
    assert not m.is_securities("https://x/api/optionsplay/api/v1/securities/markets")
    assert not m.is_securities("https://x/api/optionsplay/api/v1/securities/quotes")


def test_an_evening_list_is_that_session_and_a_morning_one_the_session_before():
    m = _module()
    assert m.securities_session(datetime(2026, 10, 15, 23, 0, tzinfo=UTC)) == "2026-10-15", "19:00 ET"
    assert m.securities_session(datetime(2026, 10, 15, 12, 0, tzinfo=UTC)) == "2026-10-14", "08:00 ET"
    assert m.securities_session(datetime(2026, 10, 17, 18, 0, tzinfo=UTC)) == "2026-10-16", "a Saturday"


def test_a_good_list_is_saved_with_only_the_kept_fields_and_never_overwritten(tmp_path):
    m = _module()
    at = datetime(2026, 10, 15, 23, 0, tzinfo=UTC)
    ok, problems = m.save_securities(_rows(m.SECURITIES_MIN_ROWS), tmp_path, at)
    assert ok and not problems
    saved = json.loads((tmp_path / "2026-10-15.json").read_text(encoding="utf-8"))
    assert saved["session"] == "2026-10-15" and len(saved["rows"]) == m.SECURITIES_MIN_ROWS
    assert set(saved["rows"][0]) == set(m.SECURITIES_FIELDS), "marketCap is not kept"
    ok, problems = m.save_securities(_rows(m.SECURITIES_MIN_ROWS), tmp_path, at)
    assert not ok and problems[0].endswith("not overwritten")


def test_a_short_or_unlabelled_list_is_set_aside(tmp_path):
    m = _module()
    at = datetime(2026, 10, 15, 23, 0, tzinfo=UTC)
    ok, problems = m.save_securities(_rows(10), tmp_path, at)
    assert not ok and "fewer than" in problems[0]
    assert (tmp_path / "2026-10-15.rejected.json").exists() and not (tmp_path / "2026-10-15.json").exists()
    ok, problems = m.save_securities(_rows(m.SECURITIES_MIN_ROWS, labelled=False), tmp_path, at)
    assert not ok and "directionalEdge" in problems[0]
