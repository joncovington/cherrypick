"""The profile-bars store is judged once its session's recorder run has had time to land.

The failure being guarded: the broker hands back only its newest 8,000 one-minute bars (about six
days of a future), so a `profile-bars` job that fails quietly loses those days for good. The store is
built from the recorder script's own SCHEMA and its SYMBOLS are the watchdog's, so neither can drift.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cherrypick.orchestrator import watchdog
from cherrypick.orchestrator.watchdog import OK, WARN

ET = ZoneInfo("America/New_York")
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_profile_bars.py"
CFG = {"technicals": {"enabled": True}}


def _script():
    spec = importlib.util.spec_from_file_location("fetch_profile_bars", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fpb = _script()


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "profile_bars.db"
    monkeypatch.setattr(watchdog, "_profile_bars_db", lambda: path)

    def fill(day: str, minutes: dict[str, int], close_min: int = 16 * 60) -> Path:
        conn = sqlite3.connect(path)
        conn.execute(fpb.SCHEMA)
        for sym, n in minutes.items():
            for i in range(n):
                m = 9 * 60 + 30 + i
                if m >= close_min:
                    break
                conn.execute(
                    "INSERT INTO profile_bars VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        sym,
                        int(day.replace("-", "")) * 10_000 + i,
                        sym,
                        day,
                        f"{m // 60:02d}:{m % 60:02d}",
                        1,
                        1,
                        1,
                        1,
                        0,
                        0,
                    ),
                )
        conn.commit()
        conn.close()
        return path

    return fill


def _enabled(cfg=CFG):
    tc = watchdog.cfgmod.technicals_settings(cfg)
    return tc["switch"] and tc["profile_bars"]


def test_the_watchdog_judges_the_symbols_the_recorder_records():
    assert watchdog.PROFILE_BARS_SYMBOLS == fpb.SYMBOLS


def test_a_whole_session_for_every_symbol_is_ok(store):
    assert _enabled()
    store("2026-11-13", dict.fromkeys(fpb.SYMBOLS, 390))
    [f] = watchdog._check_profile_bars(CFG, datetime(2026, 11, 13, 21, 0, tzinfo=ET))
    assert f.status == OK and "2026-11-13" in f.message


def test_a_short_or_missing_symbol_warns_once_per_symbol(store):
    minutes = dict.fromkeys(fpb.SYMBOLS, 390)
    minutes["/NQ"] = 200
    del minutes["NDX"]
    minutes["SPY"] = 388  # within the allowance
    store("2026-11-13", minutes)
    [f] = watchdog._check_profile_bars(CFG, datetime(2026, 11, 13, 21, 0, tzinfo=ET))
    assert f.status == WARN
    assert set(f.members) == {"2026-11-13:/NQ", "2026-11-13:NDX"}
    assert "/NQ 200/390" in f.message and "NDX 0/390" in f.message


def test_before_the_run_has_had_its_retries_the_prior_session_is_judged(store):
    store("2026-11-12", dict.fromkeys(fpb.SYMBOLS, 390))  # 11-13 not written yet at 18:00
    [f] = watchdog._check_profile_bars(CFG, datetime(2026, 11, 13, 18, 0, tzinfo=ET))
    assert f.status == OK and "2026-11-12" in f.message
    # a weekend judges Friday
    store("2026-11-13", dict.fromkeys(fpb.SYMBOLS, 390))
    [f] = watchdog._check_profile_bars(CFG, datetime(2026, 11, 15, 12, 0, tzinfo=ET))
    assert "2026-11-13" in f.message


def test_a_half_day_expects_only_its_own_minutes(store):
    # 2026-11-27, the day after Thanksgiving, closes at 13:00: 210 minutes
    store("2026-11-27", dict.fromkeys(fpb.SYMBOLS, 400), close_min=13 * 60)
    [f] = watchdog._check_profile_bars(CFG, datetime(2026, 11, 27, 21, 0, tzinfo=ET))
    assert f.status == OK and "210 minutes" in f.message


def test_a_store_never_written_warns(store):
    [f] = watchdog._check_profile_bars(CFG, datetime(2026, 11, 13, 21, 0, tzinfo=ET))
    assert f.status == WARN and f.members == ("2026-11-13:store",)


def test_nothing_is_judged_with_the_job_switched_off(store):
    off = {"technicals": {"enabled": True, "profile_bars": False}}
    assert watchdog._check_profile_bars(off, datetime(2026, 11, 13, 21, 0, tzinfo=ET)) == []
