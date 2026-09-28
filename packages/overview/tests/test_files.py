"""The daily market files: Cboe histories, Treasury's curve, the release calendars.

Each test breaks one thing a reader would otherwise trust -- a truncated download, a close dated on
the session itself, a tenor missing from a row -- and asserts the pack refuses or reports it rather
than guessing.
"""

from __future__ import annotations

import json
import math

from cherrypick.overview import facts, files


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- Cboe


def test_a_one_value_file_and_an_ohlc_file_both_give_closes():
    skew = "﻿DATE,SKEW\n09/24/2026,140.5\n09/25/2026,144.91\n"
    vix = "DATE,OPEN,HIGH,LOW,CLOSE\n09/25/2026,15.61,15.94,14.68,14.87\n"
    assert [v for _, v in files.parse_cboe(skew)] == [140.5, 144.91]
    assert [v for _, v in files.parse_cboe(vix)] == [14.87]


def test_zero_and_unparseable_rows_are_dropped_not_read():
    text = "DATE,SKEW\n09/23/2026,0\n09/24/2026,n/a\nnot a date,130\n09/25/2026,144.91\n"
    assert [v for _, v in files.parse_cboe(text)] == [144.91]


def test_a_file_that_is_not_a_cboe_history_parses_to_nothing():
    assert files.parse_cboe("<html>Access Denied</html>") == []


def test_a_download_shorter_or_older_than_the_file_on_disk_is_refused():
    old = files.parse_cboe("DATE,SKEW\n09/24/2026,140\n09/25/2026,141\n")
    shorter = files.parse_cboe("DATE,SKEW\n09/25/2026,141\n")
    older = files.parse_cboe("DATE,SKEW\n09/23/2026,139\n09/24/2026,140\n")
    newer = files.parse_cboe("DATE,SKEW\n09/24/2026,140\n09/25/2026,141\n09/28/2026,142\n")
    assert files.history_problems(old, shorter)
    assert files.history_problems(old, older)
    assert files.history_problems(old, []) == ["the new file parses to no rows"]
    assert files.history_problems(old, newer) == []
    assert files.history_problems([], newer) == []


# --------------------------------------------------------------------------- arithmetic


def test_realized_vol_matches_the_textbook_formula():
    closes = [100.0, 101.0, 100.0, 102.0, 101.0, 103.0, 102.0, 104.0, 103.0, 105.0, 104.0]
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:], strict=False)]
    mean = sum(rets) / len(rets)
    expected = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(252) * 100
    assert math.isclose(files.realized_vol(closes, 10), expected)


def test_realized_vol_needs_one_more_close_than_returns():
    assert files.realized_vol([100.0] * 10, 10) is None
    assert files.realized_vol([100.0] * 11, 10) == 0.0


def test_the_weekly_expected_move_is_vix_over_root_52():
    assert round(files.weekly_expected_move_pct(14.87), 3) == round(14.87 / math.sqrt(52), 3)


def test_the_moves_block_prices_the_band_in_spx_points_and_against_realized():
    readings = {"vix": {"value": 20.0, "basis": "live"}, "spx": {"value": 6000.0}}
    history = {"SPX": [{"session": f"2026-09-{d:02d}", "close": 6000.0} for d in range(10, 21)]}
    block = facts._moves(readings, history)
    assert block["weekly_expected_move"]["pct"] == round(20.0 / math.sqrt(52), 2)
    assert block["weekly_expected_move"]["points"] == round(
        6000.0 * block["weekly_expected_move"]["pct"] / 100, 1
    )
    assert block["realized_vol"]["pct"] == 0.0 and block["vix_minus_realized"] == 20.0


def test_the_moves_block_refuses_rather_than_guesses():
    block = facts._moves({"vix": {"value": None}, "spx": {"value": None}}, {})
    assert block["weekly_expected_move"]["reason"] == "vix_unmeasured"
    assert block["realized_vol"]["reason"] == "too_few_closes"
    assert block["vix_minus_realized"] is None


# --------------------------------------------------------------------------- Treasury

TREASURY = (
    'Date,"1 Mo","3 Mo","2 Yr","5 Yr","10 Yr","30 Yr"\n'
    "09/25/2026,4.04,4.24,4.81,4.98,5.17,5.49\n"
    "09/24/2026,4.01,4.24,4.87,5.03,5.18,5.47\n"
    "09/28/2026,4.00,4.20,4.70,4.90,5.10,5.40\n"
)


def test_the_yields_block_is_the_last_session_before_the_pack():
    _write(files.treasury_path(2026), TREASURY)
    block = facts._yields("2026-09-28")
    assert block["session"] == "2026-09-25", "a curve dated on the session itself is not read"
    assert block["yields"]["10y"] == 5.17
    assert block["spread_2s10s_bp"] == 36 and block["spread_3m10y_bp"] == 93
    assert block["change_bp"]["2y"] == -6 and block["change_bp"]["30y"] == 2


def test_january_reads_december_from_last_years_file():
    _write(files.treasury_path(2025), 'Date,"2 Yr","10 Yr"\n12/31/2025,4.10,4.60\n')
    _write(files.treasury_path(2026), 'Date,"2 Yr","10 Yr"\n')
    assert facts._yields("2026-01-02")["session"] == "2025-12-31"


def test_no_treasury_file_is_reported_not_zero():
    assert facts._yields("2026-09-28") == {"session": None, "reason": "no_treasury_file"}


# --------------------------------------------------------------------------- the release calendar


def test_releases_are_the_coming_week_in_et_with_todays_flagged():
    bea = {
        "Gross Domestic Product": {
            "release_dates": ["2026-09-24T12:30:00+00:00", "2026-09-30T12:30:00+00:00"]
        },
        "Personal Income and Outlays": {
            "release_dates": [
                "2026-09-28T12:30:00+00:00",
                "2026-09-28T12:30:00+00:00",
                "2026-10-30T12:30:00+00:00",
            ]
        },
    }
    _write(files.bea_path(), json.dumps(bea))
    rows = files.releases("2026-09-28", 7)
    assert [(r["name"], r["date"], r["time_et"], r["today"]) for r in rows] == [
        ("Personal Income and Outlays", "2026-09-28", "08:30", True),
        ("Gross Domestic Product", "2026-09-30", "08:30", False),
    ]


def test_fred_releases_join_the_calendar_without_a_time():
    _write(files.bea_path(), "{}")
    fred = {"releases": [{"name": "Consumer Price Index", "at": "2026-10-01", "source": "FRED"}]}
    _write(files.fred_releases_path(), json.dumps(fred))
    rows = files.releases("2026-09-28", 7)
    assert rows == [
        {
            "name": "Consumer Price Index",
            "date": "2026-10-01",
            "time_et": None,
            "source": "FRED",
            "today": False,
        }
    ]
