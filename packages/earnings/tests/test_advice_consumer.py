"""The advised twin — the loop side of the agentic layer for earnings.

Unlike MEIC (zero changes) and flies (one function), this module touches three places, and each of
them is here because a simpler design would have been wrong:

* **The row stamp.** Exit thresholds are read from config at DECISION time, so params held only in
  memory would govern entries today and quietly stop governing exits tomorrow.
* **The choke point.** `management.effective_config` is where a stamped row's params are restated,
  so an advised position is managed under its own terms at every later tick — and a control row is
  provably untouched.
* **The entry hook.** The twin gets byte-identical fills, so the only thing separating it from its
  control is the management params. Anything else and the P&L difference is unattributable.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cherrypick.core import advice as core_advice

from cherrypick.earnings import advice, management

DAY = "2026-08-13"
BOUNDS = {
    "iron_fly.profit_target_pct": {"min": 0.15, "max": 0.60},
    "iron_condor.profit_target_pct": {"min": 0.15, "max": 0.60},
}


@pytest.fixture
def homes(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("EARNINGS_DATA_DIR", str(tmp_path / "earnings"))
    (tmp_path / "earnings").mkdir(parents=True, exist_ok=True)
    return tmp_path


def config(**overrides):
    return {
        "strategies": {
            "iron_fly": {"profit_target_pct": 0.5, "stop_loss_credit_multiple": 2.0},
            "iron_condor": {"profit_target_pct": 0.5},
        },
        "advice": {"enabled": True, "bounds": BOUNDS, **overrides},
    }


def _write_artifact(homes: Path, proposals, session=DAY, hours=12):
    core_advice.write(
        core_advice.advice_path(homes / "home" / "state", "earnings", session),
        "earnings",
        session,
        proposals,
        advisor="test",
        expires_at=(datetime.now(UTC) + timedelta(hours=hours)).isoformat(),
    )


def _proposal(value=0.3, param="iron_fly.profit_target_pct"):
    return [{"param": param, "value": value, "rationale": "take the crush sooner"}]


SAVE_SPEC = {
    "order_id": "strat_test-iron_fly-AAPL-2026-08-13-1",
    "strategy": "iron_fly",
    "symbol": "AAPL",
    "expiration": "2026-08-15",
    "legs_json": "[]",
    "entry_credit": 3.1,
    "profile": "strat_test:iron_fly",
    "quantity": 2,
    "capital_at_risk": 690.0,
    "entry_cost": 2.6,
    "entry_slippage": 0.4,
    "entry_iv": 0.55,
}


# --------------------------------------------------------------------------- the decision


def test_dotted_params_resolve_to_the_strategy_they_name(homes):
    """`core.advice` treats a param name as opaque, so the dotted convention costs no contract
    change — the split happens here, and only for the strategy that owns it."""
    _write_artifact(homes, _proposal())
    decided = advice.decision(config(), DAY)
    assert advice.params_for(decided, "iron_fly") == {"profit_target_pct": 0.3}
    assert advice.params_for(decided, "iron_condor") == {}


def test_an_unknown_strategys_dotted_name_is_out_of_bounds(homes):
    """The bounds manifest names every legal param, dotted prefix included, so a param scoped to a
    strategy nobody declared is refused by the same validator the producer used."""
    _write_artifact(homes, _proposal(param="no_such_strategy.profit_target_pct"))
    decided = advice.decision(config(), DAY)
    assert decided["params"] is None
    assert "not in advice_bounds" in json.dumps(decided["rejected"])


def test_reject_all_means_no_twins_and_the_reason_is_recorded(homes):
    _write_artifact(homes, _proposal(0.95))
    decided = advice.decision(config(), DAY)
    assert decided["params"] is None
    assert "reject-all" in decided["reason"]
    assert advice.params_for(decided, "iron_fly") == {}


def test_absent_expired_and_stale_advice_are_all_baseline(homes):
    assert advice.decision(config(), DAY)["reason"] == "absent"

    Path(advice.decision_path()).unlink()
    _write_artifact(homes, _proposal(), hours=-1)
    assert advice.decision(config(), DAY)["params"] is None

    Path(advice.decision_path()).unlink()
    _write_artifact(homes, _proposal(), session="2026-08-12")
    assert advice.decision(config(), DAY)["params"] is None


def test_the_decision_is_read_once_and_replayed(homes):
    _write_artifact(homes, _proposal())
    first = advice.decision(config(), DAY)
    core_advice.advice_path(homes / "home" / "state", "earnings", DAY).unlink()
    assert advice.decision(config(), DAY) == first


def test_a_module_that_declares_no_advice_block_takes_none(homes):
    """Baseline, with the cause named, and — since 2026-08-25 — not written down. Earnings lost
    that session's artifact to an 03:03 entry pass whose recorded `advice_disabled` stuck all day."""
    _write_artifact(homes, _proposal())
    decided = advice.decision({"strategies": {}}, DAY)
    assert decided["reason"] == "advice_disabled: no advice block in config"
    assert not os.path.exists(advice.decision_path())

    # ...so the scan that follows, with a config that does accept advice, still sees the artifact.
    assert advice.decision(config(), DAY)["params"] is not None


def test_a_replay_does_not_fix_the_live_days_decision(homes):
    """The harness runs against arbitrary past dates (`cmd_run_entries` on a backfill). Such a run
    needs a decision; it must not be the one the live session recorded."""
    _write_artifact(homes, _proposal())
    decided = advice.decision(config(), DAY, persist=False)
    assert decided["params"] is not None
    assert not os.path.exists(advice.decision_path())


# --------------------------------------------------------------------------- the twin


def test_the_twin_is_identical_in_everything_but_its_params():
    twin = advice.twin_spec(SAVE_SPEC, {"profit_target_pct": 0.3})
    assert twin["profile"] == "advised:strat_test:iron_fly"
    assert twin["order_id"].startswith("advised-")
    assert json.loads(twin["advice_params"]) == {"profit_target_pct": 0.3}
    # Every fill field is the control's, byte for byte — the comparison isolates management.
    for field in ("legs_json", "entry_credit", "quantity", "capital_at_risk", "entry_cost",
                  "entry_slippage", "entry_iv", "symbol", "expiration", "strategy"):
        assert twin[field] == SAVE_SPEC[field]


def test_the_twin_tag_is_what_the_verdict_groups_on():
    assert advice.advised_book("strat_test:iron_fly") == "advised:strat_test:iron_fly"
    assert advice.is_advised("advised:strat_test:iron_fly") is True
    assert advice.is_advised("strat_test:iron_fly") is False


# --------------------------------------------------------------------------- the choke point


def test_effective_config_overlays_only_the_stamped_strategy():
    trade = {"strategy": "iron_fly", "advice_params": json.dumps({"profit_target_pct": 0.25})}
    overlaid = management.effective_config(trade, config())
    assert overlaid["strategies"]["iron_fly"]["profit_target_pct"] == 0.25
    # Everything else in that strategy's block, and every other strategy, is untouched.
    assert overlaid["strategies"]["iron_fly"]["stop_loss_credit_multiple"] == 2.0
    assert overlaid["strategies"]["iron_condor"]["profit_target_pct"] == 0.5


def test_a_control_row_gets_its_config_back_unchanged():
    base = config()
    for trade in ({"strategy": "iron_fly"}, {"strategy": "iron_fly", "advice_params": None},
                  {"strategy": "iron_fly", "advice_params": "{}"}):
        assert management.effective_config(trade, base) is base or \
            management.effective_config(trade, base) == base


def test_an_unreadable_stamp_falls_back_to_the_control_never_to_a_guess():
    trade = {"strategy": "iron_fly", "advice_params": "{ half written"}
    assert management.effective_config(trade, config()) == config()


def test_the_overlay_is_pure_and_does_not_mutate_the_shared_config():
    base = config()
    trade = {"strategy": "iron_fly", "advice_params": json.dumps({"profit_target_pct": 0.25})}
    management.effective_config(trade, base)
    assert base["strategies"]["iron_fly"]["profit_target_pct"] == 0.5


def test_an_overlaid_target_closes_a_position_the_control_still_holds():
    """The behaviour the whole design exists for, at the level the loop actually decides at."""
    from cherrypick.earnings.strategies import iron_fly

    trade = {
        "strategy": "iron_fly",
        "entry_credit": 4.0,
        "legs_json": json.dumps([
            {"symbol": "AAPL  260815C00200000", "action": "Sell to Open", "quantity": 1},
        ]),
    }
    quotes = {"AAPL  260815C00200000": {"bid": 2.6, "ask": 2.8}}

    control = iron_fly.evaluate_position(dict(trade), quotes, config())
    advised = iron_fly.evaluate_position(
        dict(trade),
        quotes,
        management.effective_config(
            {**trade, "advice_params": json.dumps({"profit_target_pct": 0.25})}, config()
        ),
    )
    assert control["action"] == "hold"
    assert advised["action"] == "close_all"
    assert "profit_target" in advised["reason"]


# --------------------------------------------------------------------------- continuity


def test_an_advised_row_keeps_its_params_after_advice_goes_away(homes):
    """Exit continuity is free by construction: the params are on the row, not in the session."""
    _write_artifact(homes, _proposal(0.25))
    decided = advice.decision(config(), DAY)
    twin = advice.twin_spec(SAVE_SPEC, advice.params_for(decided, "iron_fly"))

    core_advice.advice_path(homes / "home" / "state", "earnings", DAY).unlink()
    Path(advice.decision_path()).unlink()

    # Tomorrow: no advice at all, and the row still governs its own exits.
    assert advice.decision(config(), "2026-08-14")["params"] is None
    overlaid = management.effective_config(
        {"strategy": "iron_fly", "advice_params": twin["advice_params"]}, config()
    )
    assert overlaid["strategies"]["iron_fly"]["profit_target_pct"] == 0.25


# --------------------------------------------------------------------------- the migration


def test_the_migration_adds_the_column_without_losing_rows(tmp_path, monkeypatch):
    """An additive column on a ledger that already holds trades — the only safe kind here."""
    from cherrypick.earnings import db_paper

    db = tmp_path / "paper_trades.db"
    # DB_PATH is resolved at import, so the env var alone would point a module that some other
    # test already imported at the wrong file — patch the attribute, as the db tests do.
    monkeypatch.setattr(db_paper, "DB_PATH", db)
    conn = sqlite3.connect(db)
    # A ledger as it stood before this column existed: the columns the existing backfills read
    # (closed_at, close_attempts) are present, `advice_params` is not.
    conn.execute(
        "CREATE TABLE trades (order_id TEXT PRIMARY KEY, strategy TEXT, symbol TEXT,"
        " expiration TEXT, closed_at REAL, close_attempts INTEGER DEFAULT 0, pnl REAL)"
    )
    conn.execute(
        "INSERT INTO trades (order_id, strategy, symbol, expiration, closed_at, pnl)"
        " VALUES ('old-1', 'iron_fly', 'AAPL', '2026-08-15', 1754000000.0, 42.0)"
    )
    conn.commit()
    conn.close()

    migrated = db_paper._conn()
    columns = {r[1] for r in migrated.execute("PRAGMA table_info(trades)")}
    assert "advice_params" in columns
    assert migrated.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
    assert migrated.execute("SELECT pnl FROM trades").fetchone()[0] == 42.0
    assert migrated.execute("SELECT advice_params FROM trades").fetchone()[0] is None
    migrated.close()
