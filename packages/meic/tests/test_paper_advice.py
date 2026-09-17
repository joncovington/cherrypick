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
from cherrypick.core import advice as core_advice

from cherrypick.meic import paper, paper_loop

DAY = "2026-07-29"
BOUNDS = {"stop_trigger_ratio": {"min": 0.85, "max": 0.95}}
CFG = {"advice": {"enabled": True, "base_profile": "control", "bounds": BOUNDS}}


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """Isolated cherrypick home (advice artifacts) + MEIC data home (decision file, paper DB)."""
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MEIC_DATA_DIR", str(tmp_path / "meic"))
    db = tmp_path / "meic" / "paper_trades.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "cherrypick.meic.db", "--db", str(db), "init_db"],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(paper_loop, "_PAPER_DB", str(db))
    return tmp_path


def _write_artifact(home: Path, proposals, session=DAY, experiment_id=None, experiments=None):
    """Legacy single-overlay shape by default (its one entry resolves to `advised:control`); with
    `experiments`, one entry per concurrent experiment (2026-09-17)."""
    state = home / "home" / "state"
    expires = (datetime.now(UTC) + timedelta(hours=12)).isoformat()
    core_advice.write(
        core_advice.advice_path(state, "meic", session),
        "meic",
        session,
        proposals,
        advisor="test",
        expires_at=expires,
        experiment_id=experiment_id,
        experiments=experiments,
    )


def _experiment(name, value, experiment_id, base="control"):
    return {
        "experiment_id": experiment_id,
        "name": name,
        "tag": core_advice.advised_tag(name),
        "base": base,
        "proposals": [{"param": "stop_trigger_ratio", "value": value, "rationale": name}],
        "rejected": [],
    }


def test_each_experiment_builds_its_own_book_named_for_it(homes):
    """Many experiments per module (2026-09-17): two entries on control are two shadow books of
    the same control, each carrying exactly its own overlay and its own experiment stamp."""
    _write_artifact(
        homes,
        [],
        experiments=[
            _experiment("Stop Early (probe)", 0.88, "exp-2026-09-17-meic-1"),
            _experiment("stop-late", 0.94, "exp-2026-09-17-meic-2"),
        ],
    )
    profiles, reason = paper_loop._advice_profiles(CFG, DAY)
    assert set(profiles) == {"advised:stop-early-probe", "advised:stop-late"}
    assert profiles["advised:stop-early-probe"]["stop_trigger_ratio"] == 0.88
    assert profiles["advised:stop-early-probe"]["experiment_id"] == "exp-2026-09-17-meic-1"
    assert profiles["advised:stop-late"]["stop_trigger_ratio"] == 0.94
    assert profiles["advised:stop-late"]["experiment_id"] == "exp-2026-09-17-meic-2"
    base = paper.load_profiles()["control"]
    for adv in profiles.values():
        assert {k: v for k, v in adv.items() if k not in ("stop_trigger_ratio", "experiment_id")} == {
            k: v for k, v in base.items() if k != "stop_trigger_ratio"
        }
    assert reason is None


def test_one_experiments_rejection_is_its_baseline_day_alone(homes):
    _write_artifact(
        homes,
        [],
        experiments=[
            _experiment("too-loose", 0.99, "exp-2026-09-17-meic-1"),
            _experiment("in-bounds", 0.9, "exp-2026-09-17-meic-2"),
        ],
    )
    profiles, reason = paper_loop._advice_profiles(CFG, DAY)
    assert set(profiles) == {"advised:in-bounds"}
    assert "reject-all" in reason  # the first entry's reason, mirrored at the top level


def test_an_advised_book_shadows_the_base_its_entry_names(homes):
    """The base is read from the entry, never split out of the tag."""
    registry = paper.load_profiles()
    other = next(name for name in registry if name != "control")
    _write_artifact(homes, [], experiments=[_experiment("other-twin", 0.9, "exp-1", base=other)])
    profiles, _ = paper_loop._advice_profiles(CFG, DAY)
    adv = profiles["advised:other-twin"]
    assert {k: v for k, v in adv.items() if k not in ("stop_trigger_ratio", "experiment_id")} == {
        k: v for k, v in registry[other].items() if k != "stop_trigger_ratio"
    }


def test_a_legacy_decision_file_still_builds_advised_control(homes):
    """A decision recorded before `experiments` existed resolves to the one `advised:<base>` book
    its rows were always tagged with."""
    path = paper_loop._paths.data_path("advice_active.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "day": DAY,
                "base_profile": "control",
                "params": {"stop_trigger_ratio": 0.9},
                "reason": None,
                "experiment_id": "exp-2026-09-09-meic-1",
            }
        ),
        encoding="utf-8",
    )
    profiles, _ = paper_loop._advice_profiles(CFG, DAY)
    assert set(profiles) == {"advised:control"}
    assert profiles["advised:control"]["stop_trigger_ratio"] == 0.9
    assert profiles["advised:control"]["experiment_id"] == "exp-2026-09-09-meic-1"


def test_an_open_experiment_book_gets_a_management_twin_on_its_base(homes):
    """`advised:<name>` holding rows with no decision naming it: the twin is built on the
    configured base, because the tag no longer carries one to split out."""
    conn = sqlite3.connect(paper_loop._PAPER_DB)
    ts = f"{DAY}T13:00:00"
    conn.execute(
        "INSERT INTO ic_trades (ic_order_id, trade_date, symbol, risk_profile, status, "
        "created_at, updated_at) VALUES ('A1', ?, 'SPX', 'advised:stop-early-probe', 'open', ?, ?)",
        (DAY, ts, ts),
    )
    conn.commit()
    conn.close()
    profiles, _ = paper_loop._advice_profiles({"advice": {"enabled": False}}, DAY)
    twin = profiles["advised:stop-early-probe"]
    base = paper.load_profiles()["control"]
    assert twin == {**base, "max_concurrent_ics": 0}


def test_valid_advice_builds_the_advised_book(homes):
    _write_artifact(
        homes,
        [{"param": "stop_trigger_ratio", "value": 0.9, "rationale": "r"}],
        experiment_id="exp-2026-09-09-meic-1",
    )
    profiles, reason = paper_loop._advice_profiles(CFG, DAY)
    assert "advised:control" in profiles
    adv = profiles["advised:control"]
    assert adv["stop_trigger_ratio"] == 0.9
    # The experiment rides on the synthetic def (not a trading parameter -- the fill row copies it).
    assert adv["experiment_id"] == "exp-2026-09-09-meic-1"
    # The rest of the def is the base profile's — the advised twin differs in exactly the advice.
    base = paper.load_profiles()["control"]
    assert {k: v for k, v in adv.items() if k not in ("stop_trigger_ratio", "experiment_id")} == {
        k: v for k, v in base.items() if k != "stop_trigger_ratio"
    }
    assert "experiment_id" not in base
    assert reason is None


def test_the_fill_row_copies_the_experiment_from_its_profile():
    leg = {"strike": 6000.0, "streamer_symbol": ".X", "bid": 1.0, "ask": 1.2, "delta": -0.1}
    chosen = {
        "short_put": leg,
        "long_put": {**leg, "strike": 5990.0},
        "short_call": {**leg, "strike": 6010.0},
        "long_call": {**leg, "strike": 6020.0},
        "wing_width": 10,
        "put_credit": 0.5,
        "call_credit": 0.5,
        "net_credit": 1.0,
        "open_fee": 6.0,
    }
    snapshot = {"symbol": "SPX", "date": DAY, "now_et": "13:00", "underlying_price": 6005.0}
    params = {"stop_trigger_ratio": 0.9, "experiment_id": "exp-2026-09-09-meic-1"}
    row = paper.synthetic_entry_fill(snapshot, "advised:control", chosen, params, "paper")
    assert row["experiment_id"] == "exp-2026-09-09-meic-1"
    control = paper.synthetic_entry_fill(snapshot, "control", chosen, {"stop_trigger_ratio": 0.9}, "paper")
    assert control["experiment_id"] is None


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
    assert "advised:control" in profiles3


def test_disabled_config_is_baseline_and_never_recorded(homes):
    """Baseline, and — since 2026-08-25 — NOT written down. meic lost that session's artifact to a
    forced 01:05 ET iteration whose recorded `advice_disabled` the market-open iteration replayed."""
    _write_artifact(homes, [{"param": "stop_trigger_ratio", "value": 0.9, "rationale": "r"}])
    profiles, reason = paper_loop._advice_profiles({"advice": {"enabled": False}}, DAY)
    assert profiles == {}
    assert reason == "advice_disabled: advice.enabled is false"
    assert not paper_loop._paths.data_path("advice_active.json").exists()

    # ...so the next process, reading a config that does accept advice, still gets the artifact.
    profiles2, _ = paper_loop._advice_profiles(CFG, DAY)
    assert "advised:control" in profiles2


def test_a_forced_iteration_does_not_fix_the_days_decision(homes):
    """`--once --force` runs outside the trading window on purpose. It gets a decision to run
    under; it does not get to be the one the session recorded."""
    _write_artifact(homes, [{"param": "stop_trigger_ratio", "value": 0.9, "rationale": "r"}])
    profiles, _ = paper_loop._advice_profiles(CFG, DAY, persist=False)
    assert "advised:control" in profiles
    assert not paper_loop._paths.data_path("advice_active.json").exists()


def test_open_advised_positions_get_a_management_only_twin(homes):
    conn = sqlite3.connect(paper_loop._PAPER_DB)
    ts = f"{DAY}T13:00:00"
    conn.execute(
        "INSERT INTO ic_trades (ic_order_id, trade_date, symbol, risk_profile, status, "
        "created_at, updated_at) VALUES ('A1', ?, 'SPX', 'advised:control', 'open', ?, ?)",
        (DAY, ts, ts),
    )
    conn.commit()
    conn.close()
    profiles, _ = paper_loop._advice_profiles({"advice": {"enabled": False}}, DAY)
    twin = profiles["advised:control"]
    assert twin["max_concurrent_ics"] == 0  # exits run; entries cannot
    base = paper.load_profiles()["control"]
    assert twin["stop_trigger_ratio"] == base.get("stop_trigger_ratio", twin["stop_trigger_ratio"])


def test_process_symbol_evaluates_extra_profiles(homes):
    """The engine seam: a synthetic advised profile is evaluated beside the registry ones."""
    # Read the traded symbol from config rather than naming one: a symbol outside the configured
    # set gets no per-profile results at all, so a hardcoded name silently breaks this test every
    # time the symbol set changes (XSP on 2026-07-28, SPX on 2026-08-01 — twice now).
    symbol = (paper.load_base_config().get("symbols") or ["SPX"])[0]
    snapshot = {
        "symbol": symbol,
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
    base = paper.load_profiles()["control"]
    result = paper.process_symbol(
        snapshot,
        paper_loop._PAPER_DB,
        "paper",
        extra_profiles={"advised:control": {**base, "stop_trigger_ratio": 0.9}},
    )
    assert "advised:control" in result["results"]


def test_decision_file_lives_in_the_data_home(homes):
    paper_loop._advice_profiles(CFG, DAY)
    decision = json.loads((homes / "meic" / "advice_active.json").read_text(encoding="utf-8"))
    assert decision["day"] == DAY and decision["reason"] == "absent"
