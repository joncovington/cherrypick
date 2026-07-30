"""The advised shadow book (paper_loop._advice_profiles + paper.process_symbol extra_profiles).

Loop-side of the advise pipeline: re-validation with cherrypick.core.advice, read-once-per-
session persistence across --once processes, baseline on absent/invalid, and the management-only
twin that keeps open advised positions exiting when today's advice is off.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(_SRC))
# Bootstrap src/_core BEFORE the core import: unlike the sibling tests (which import a module file that
# bootstraps it), this file imports cherrypick.core directly, so on its own it failed collection unless
# another test module happened to run first.
_CORE = _SRC / "_core"
if _CORE.is_dir() and str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))
from cherrypick.core import advice as core_advice  # noqa: E402

import paper  # noqa: E402
import paper_loop  # noqa: E402

DAY = "2026-07-29"
BOUNDS = {"stop_trigger_ratio": {"min": 0.85, "max": 0.95}}
CFG = {"advice": {"enabled": True, "base_profile": "conservative", "bounds": BOUNDS}}


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """Isolated cherrypick home (advice artifacts) + MEIC data home (decision file, paper DB)."""
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MEIC_DATA_DIR", str(tmp_path / "meic"))
    db = tmp_path / "meic" / "paper_trades.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, str(Path(__file__).parent.parent / "src" / "db.py"), "--db", str(db), "init_db"],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(paper_loop, "_PAPER_DB", str(db))
    return tmp_path


def _write_artifact(home: Path, proposals, session=DAY):
    state = home / "home" / "state"
    expires = (datetime.now(UTC) + timedelta(hours=12)).isoformat()
    core_advice.write(
        core_advice.advice_path(state, "meic", session),
        "meic",
        session,
        proposals,
        advisor="test",
        expires_at=expires,
    )


def test_valid_advice_builds_the_advised_book(homes):
    _write_artifact(homes, [{"param": "stop_trigger_ratio", "value": 0.9, "rationale": "r"}])
    profiles, reason = paper_loop._advice_profiles(CFG, DAY)
    assert "advised:conservative" in profiles
    adv = profiles["advised:conservative"]
    assert adv["stop_trigger_ratio"] == 0.9
    # The rest of the def is the base profile's — the control differs in exactly the advice.
    base = paper.load_profiles()["conservative"]
    assert {k: v for k, v in adv.items() if k != "stop_trigger_ratio"} == {
        k: v for k, v in base.items() if k != "stop_trigger_ratio"
    }
    assert reason is None


def test_absent_advice_is_baseline(homes):
    profiles, reason = paper_loop._advice_profiles(CFG, DAY)
    assert profiles == {} and reason == "absent"


def test_out_of_bounds_advice_is_baseline(homes):
    _write_artifact(homes, [{"param": "stop_trigger_ratio", "value": 0.99, "rationale": "r"}])
    profiles, reason = paper_loop._advice_profiles(CFG, DAY)
    assert profiles == {}
    assert "reject-all" in reason


def test_decision_is_read_once_per_session(homes):
    """The first iteration's decision persists; a later artifact must not change the session."""
    profiles, _ = paper_loop._advice_profiles(CFG, DAY)
    assert profiles == {}  # decided: baseline (absent)
    _write_artifact(homes, [{"param": "stop_trigger_ratio", "value": 0.9, "rationale": "late"}])
    profiles2, reason2 = paper_loop._advice_profiles(CFG, DAY)
    assert profiles2 == {} and reason2 == "absent"  # replayed, not re-read
    # A NEW session re-derives its own decision.
    _write_artifact(
        homes, [{"param": "stop_trigger_ratio", "value": 0.9, "rationale": "r"}], session="2026-07-30"
    )
    profiles3, _ = paper_loop._advice_profiles(CFG, "2026-07-30")
    assert "advised:conservative" in profiles3


def test_disabled_config_is_baseline_and_recorded(homes):
    _write_artifact(homes, [{"param": "stop_trigger_ratio", "value": 0.9, "rationale": "r"}])
    profiles, reason = paper_loop._advice_profiles({"advice": {"enabled": False}}, DAY)
    assert profiles == {} and reason == "advice_disabled"


def test_open_advised_positions_get_a_management_only_twin(homes):
    conn = sqlite3.connect(paper_loop._PAPER_DB)
    ts = f"{DAY}T13:00:00"
    conn.execute(
        "INSERT INTO ic_trades (ic_order_id, trade_date, symbol, risk_profile, status, "
        "created_at, updated_at) VALUES ('A1', ?, 'SPX', 'advised:conservative', 'open', ?, ?)",
        (DAY, ts, ts),
    )
    conn.commit()
    conn.close()
    profiles, _ = paper_loop._advice_profiles({"advice": {"enabled": False}}, DAY)
    twin = profiles["advised:conservative"]
    assert twin["max_concurrent_ics"] == 0  # exits run; entries cannot
    base = paper.load_profiles()["conservative"]
    assert twin["stop_trigger_ratio"] == base.get("stop_trigger_ratio", twin["stop_trigger_ratio"])


def test_process_symbol_evaluates_extra_profiles(homes):
    """The engine seam: a synthetic advised profile is evaluated beside the registry ones."""
    # XSP — the head of the configured symbol set since the 2026-07-28 width-study reduction
    # (a symbol outside the traded set gets no per-profile results at all).
    snapshot = {
        "symbol": "XSP",
        "date": DAY,
        "now_et": "13:00",
        "expiration": DAY,
        "dte": 0,
        "underlying_price": 590.0,
        "iv_rank": 0.5,
        "vix": 15.0,
        "session_quality": "midday",
        "gex": {"ok": False},
        "candidates": [],
        "leg_quotes": {},
    }
    base = paper.load_profiles()["conservative"]
    result = paper.process_symbol(
        snapshot,
        paper_loop._PAPER_DB,
        "paper",
        extra_profiles={"advised:conservative": {**base, "stop_trigger_ratio": 0.9}},
    )
    assert "advised:conservative" in result["results"]


def test_decision_file_lives_in_the_data_home(homes):
    paper_loop._advice_profiles(CFG, DAY)
    decision = json.loads((homes / "meic" / "advice_active.json").read_text(encoding="utf-8"))
    assert decision["day"] == DAY and decision["reason"] == "absent"
