"""Treasury's yield curve (cherrypick.core.treasury): the parser, the two-year read, the one yield."""

from __future__ import annotations

from datetime import date

from cherrypick.core import treasury

CSV_2026 = (
    '﻿Date,"1 Mo","3 Mo","2 Yr","5 Yr","10 Yr","30 Yr"\n'
    "01/05/2026,3.71,3.68,3.47,3.71,4.17,4.85\n"
    "01/02/2026,3.72,3.71,3.47,3.74,4.19,4.86\n"
)
CSV_2025 = 'Date,"2 Yr","10 Yr"\n12/31/2025,4.10,4.60\n12/30/2025,4.12,\n'


def test_the_parser_keeps_the_declared_tenors_oldest_first_and_drops_a_blank_cell():
    rows = treasury.parse(CSV_2026)
    assert [d for d, _ in rows] == [date(2026, 1, 2), date(2026, 1, 5)]
    assert rows[0][1] == {"3m": 3.71, "2y": 3.47, "5y": 3.74, "10y": 4.19, "30y": 4.86}
    assert treasury.parse(CSV_2025)[0][1] == {"2y": 4.12}  # the blank 10y is left out, not zero
    assert treasury.parse("nonsense\n1,2\n") == []
    assert treasury.parse("") == []


def test_the_read_spans_two_years_and_stops_before_the_session(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    treasury.path(2025).parent.mkdir(parents=True)
    treasury.path(2025).write_text(CSV_2025, encoding="utf-8")
    treasury.path(2026).write_text(CSV_2026, encoding="utf-8")
    assert [d for d, _ in treasury.curves_before("2026-01-05")] == [
        date(2025, 12, 30),
        date(2025, 12, 31),
        date(2026, 1, 2),
    ]
    assert treasury.yield_before("2026-01-05") == (date(2026, 1, 2), 4.19)
    assert treasury.yield_before("2026-01-02") == (date(2025, 12, 31), 4.60)
    # The 30th carries no 10y, so the 2y is the newest 2y and the 10y skips back to the 31st.
    assert treasury.yield_before("2025-12-31", "2y") == (date(2025, 12, 30), 4.12)
    assert treasury.yield_before("2025-12-31", "10y") is None
    assert treasury.yield_before("2026-01-06", "7y") is None


def test_no_file_is_empty_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    assert treasury.curves_before("2026-01-05") == []
    assert treasury.yield_before("2026-01-05") is None
