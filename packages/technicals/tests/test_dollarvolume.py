"""Each name's dollar volume (dollarvolume.py): the median rule and which file a reader gets."""

from __future__ import annotations

import json

from cherrypick.technicals import dollarvolume, paths


def _file(day: str, names: dict) -> None:
    root = paths.dollar_volume_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{day}.json").write_text(json.dumps({"session": day, "through": "x", "names": names}), "utf-8")


def test_the_median_reads_the_last_fifty_finished_sessions():
    bars = [(f"2026-07-{d:02d}", 10.0, 1_000.0 * d) for d in range(1, 31)]
    bars += [(f"2026-08-{d:02d}", 10.0, 1_000.0 * (30 + d)) for d in range(1, 31)]
    median, sessions = dollarvolume.median_dollar_volume(bars, today="2026-10-19")
    # 60 sessions held, the last 50 are volumes 11,000..60,000; their median is 35,500 x $10.
    assert sessions == 50 and median == 355_000.0


def test_the_session_in_progress_never_counts():
    bars = [("2026-10-16", 10.0, 100.0), ("2026-10-19", 10.0, 1e9)]
    assert dollarvolume.median_dollar_volume(bars, today="2026-10-19") == (1_000.0, 1)


def test_a_bar_without_a_real_close_is_not_a_session():
    bars = [("2026-10-14", 0.0, 5.0), ("2026-10-15", None, 5.0), ("2026-10-16", 2.0, 5.0)]
    assert dollarvolume.median_dollar_volume(bars, today="2026-10-19") == (10.0, 1)
    assert dollarvolume.median_dollar_volume([("2026-10-16", 0.0, 5.0)], today="2026-10-19") is None


def test_a_reader_gets_the_newest_file_on_or_before_its_day():
    _file("2026-10-15", {"A": {"median": 1, "sessions": 50}})
    _file("2026-10-16", {"A": {"median": 2, "sessions": 50}})
    _file("2026-10-19", {"A": {"median": 3, "sessions": 50}})
    names, header = dollarvolume.latest("2026-10-18")
    assert header["file"] == "2026-10-16" and names["A"]["median"] == 2
    assert dollarvolume.latest("2026-10-14") == ({}, {"file": None})


def test_each_reader_sets_its_own_minimum_sessions():
    names = {
        "FULL": {"median": 5, "sessions": 50},
        "NEW": {"median": 7, "sessions": 25},
        "BAD": {"sessions": 50},
    }
    assert dollarvolume.medians(names, 20) == {"FULL": 5.0, "NEW": 7.0}
    assert dollarvolume.medians(names, 50) == {"FULL": 5.0}
