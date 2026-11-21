"""meic.ahead: an arm's dated gates in words, held to the engine that applies them."""

import re
from datetime import date
from pathlib import Path

from cherrypick.meic import ahead

SRC = Path(ahead.__file__).parent


def _keys(text: str) -> set[str]:
    return set(re.findall(r"""(?:params|base)\.get\(\s*["'](\w+)["']""", text))


def test_every_key_read_here_is_one_the_engine_reads():
    engine = (SRC / "paper.py").read_text(encoding="utf-8")
    keys = _keys((SRC / "ahead.py").read_text(encoding="utf-8"))
    assert keys, "the key scan found nothing: the pattern no longer matches ahead.py"
    missing = sorted(k for k in keys if f'"{k}"' not in engine)
    assert not missing, f"ahead.py words gates paper.py does not read: {missing}"


def test_the_key_check_fails_on_a_key_the_engine_does_not_read():
    planted = 'params.get("fomc_blackout_strat", "13:30")'
    engine = (SRC / "paper.py").read_text(encoding="utf-8")
    assert [k for k in _keys(planted) if f'"{k}"' not in engine] == ["fomc_blackout_strat"]


def test_the_fixed_times_are_the_engines():
    engine = (SRC / "paper.py").read_text(encoding="utf-8")
    for hhmm in ("12:30", "14:00"):
        assert f'"{hhmm}"' in engine


def test_fomc_words_carry_the_arms_own_window():
    gates = {
        g["label"]: g
        for g in ahead.date_gates({"fomc_blackout_start": "13:00", "fomc_blackout_end": "15:00"}, {})
    }
    assert gates["FOMC"]["effect"].startswith("No new entries 13:00 to 15:00.")
    assert "after 15:00" in gates["FOMC"]["depends_on"]
    assert set(gates) == {"FOMC", "TripleWitching", "QuarterlyExpiry", "EarlyClose"}


def test_a_value_gate_set_where_it_cannot_bind_is_left_out():
    off = {
        "min_iv_rank": 0,
        "regime_vix_pause_threshold": 999,
        "regime_vix1d_ratio_pause_threshold": 999,
        "regime_atr_pause_threshold_pct": 999,
        "regime_gex_block_negative": False,
    }
    assert ahead.value_gates(off) == []
    on = ahead.value_gates({"regime_atr_pause_threshold_pct": 0.015, "stabilization_entry": True})
    assert [v["name"] for v in on] == ["IV rank", "VIX", "VIX1D ratio", "ATR", "GEX", "Stabilization"]
    assert all(v["depends_on"] for v in on)


def test_the_half_day_is_listed_with_meics_unmoved_times():
    doc = ahead.ahead({}, {}, date(2026, 11, 27))
    first = doc["sessions"][0]
    assert first["early_close"] and [g["label"] for g in first["gates"]] == ["EarlyClose"]
    assert doc["records"] is None
