"""Item 26's bar fetch (scripts/fetch_ladder_bars.py): candidates, stamps, Yahoo parsing, storage.

Here, not in the orchestrator's tests, because the script imports `cherrypick.technicals`, which
only this package's CI job installs. Nothing reaches the network or the broker.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_ladder_bars.py"


def _module():
    spec = importlib.util.spec_from_file_location("fetch_ladder_bars", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


flb = _module()


def _ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


def test_candidates_are_long_leveraged_only():
    names = flb.candidates()
    assert "SOXL" in names and "TECL" in names and "TQQQ" in names
    assert "SOXS" not in names  # inverse
    assert "UVXY" not in names  # volatility


def test_stamp_is_session_date_or_new_york_bar_start():
    moment = datetime(2026, 10, 29, 20, 5, tzinfo=UTC)  # 16:05 New York
    assert flb.stamp(moment, "5m") == "2026-10-29T16:05"
    assert flb.stamp(datetime(2026, 10, 29, tzinfo=UTC), "1d") == "2026-10-29"


def test_yahoo_parse_dates_daily_bars_by_new_york_session_and_drops_gaps():
    body = {
        "chart": {
            "result": [
                {
                    "timestamp": [_ts("2026-10-28T09:30:00-04:00"), _ts("2026-10-29T09:30:00-04:00")],
                    "indicators": {
                        "quote": [
                            {
                                "open": [100.0, None],
                                "high": [104.0, 105.0],
                                "low": [99.0, 97.0],
                                "close": [101.0, 102.0],
                                "volume": [10, 20],
                            }
                        ]
                    },
                    "events": {
                        "splits": {
                            "x": {"date": _ts("2021-03-02T09:30:00-05:00"), "numerator": 15, "denominator": 1}
                        }
                    },
                }
            ]
        }
    }
    parsed = flb.yahoo_parse(body, "1d")
    assert parsed["rows"] == [("2026-10-28", 100.0, 104.0, 99.0, 101.0, 10.0)]  # the gap bar is left out
    assert parsed["splits"] == [("2021-03-02", 15.0, 1.0)]
    assert flb.yahoo_parse({"chart": {"result": []}}, "1d") is None


def test_save_replaces_a_series_whole_and_keeps_other_sources(tmp_path):
    conn = flb.connect(tmp_path / "bars.db")
    flb.save(conn, "SOXL", "yahoo", "1d", [("2026-10-28", 1, 2, 0.5, 1.5, 9)], "2026-10-29")
    flb.save(
        conn,
        "SOXL",
        "broker",
        "1d",
        [("2026-10-27", 1, 2, 0.5, 1.5, 9), ("2026-10-28", 1, 2, 0.5, 1.6, 9)],
        "2026-10-29",
    )
    flb.save(conn, "SOXL", "broker", "1d", [("2026-10-29", 1, 2, 0.5, 1.7, 9)], "2026-10-30")
    rows = conn.execute("SELECT source, start FROM bars ORDER BY source, start").fetchall()
    assert rows == [("broker", "2026-10-29"), ("yahoo", "2026-10-28")]
    assert conn.execute("SELECT rows, first, last FROM fetched WHERE source = 'broker'").fetchone() == (
        1,
        "2026-10-29",
        "2026-10-29",
    )
