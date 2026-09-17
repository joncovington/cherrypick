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


def _write_artifact(homes: Path, proposals, session=DAY, hours=12, experiments=None):
    """Legacy single-overlay shape by default (its one entry resolves to the legacy twin tag);
    with `experiments`, one entry per concurrent experiment (2026-09-17)."""
    core_advice.write(
        core_advice.advice_path(homes / "home" / "state", "earnings", session),
        "earnings",
        session,
        proposals,
        advisor="test",
        expires_at=(datetime.now(UTC) + timedelta(hours=hours)).isoformat(),
        experiments=experiments,
    )


def _experiment(name, experiment_id, *proposals):
    return {
        "experiment_id": experiment_id,
        "name": name,
        "tag": core_advice.advised_tag(name),
        "base": "control",
        "proposals": [{"param": p, "value": v, "rationale": name} for p, v in proposals],
        "rejected": [],
    }


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


def test_each_experiment_opens_its_own_twin_of_the_strategy_it_names(homes):
    """Many experiments per module (2026-09-17): two entries touching iron_fly are two twins of the
    same iron_fly control, each under `advised:<experiment name>:<strategy>` with its own params
    and stamp; an entry that names only iron_condor opens nothing for iron_fly."""
    _write_artifact(
        homes,
        [],
        experiments=[
            _experiment(
                "Fly Target (early)", "exp-2026-09-17-earnings-1", ("iron_fly.profit_target_pct", 0.25)
            ),
            _experiment("fly-target-late", "exp-2026-09-17-earnings-2", ("iron_fly.profit_target_pct", 0.55)),
            _experiment("condor-only", "exp-2026-09-17-earnings-3", ("iron_condor.profit_target_pct", 0.3)),
        ],
    )
    decided = advice.decision(config(), DAY)
    twins = advice.twins_for(decided, "iron_fly")
    assert [(t["name"], t["experiment_id"], t["params"]) for t in twins] == [
        ("Fly Target (early)", "exp-2026-09-17-earnings-1", {"profit_target_pct": 0.25}),
        ("fly-target-late", "exp-2026-09-17-earnings-2", {"profit_target_pct": 0.55}),
    ]
    assert [t["name"] for t in advice.twins_for(decided, "iron_condor")] == ["condor-only"]

    specs = [advice.twin_spec(SAVE_SPEC, t["params"], t["experiment_id"], name=t["name"]) for t in twins]
    assert [t["profile"] for t in specs] == [
        "advised:fly-target-early:iron_fly",
        "advised:fly-target-late:iron_fly",
    ]
    assert [t["experiment_id"] for t in specs] == ["exp-2026-09-17-earnings-1", "exp-2026-09-17-earnings-2"]
    # Two twins of one entry must not collide in the ledger: the order_id carries the slug.
    assert len({t["order_id"] for t in specs}) == 2
    assert all(t["order_id"].endswith(SAVE_SPEC["order_id"]) for t in specs)


def test_one_experiments_rejection_is_its_baseline_day_alone(homes):
    _write_artifact(
        homes,
        [],
        experiments=[
            _experiment("too-greedy", "exp-1", ("iron_fly.profit_target_pct", 0.95)),
            _experiment("in-bounds", "exp-2", ("iron_fly.profit_target_pct", 0.3)),
        ],
    )
    decided = advice.decision(config(), DAY)
    assert [t["name"] for t in advice.twins_for(decided, "iron_fly")] == ["in-bounds"]
    assert "reject-all" in decided["reason"]  # the first entry's, mirrored at the top level


def test_a_legacy_decision_file_still_opens_the_legacy_twin_tag(homes):
    """A decision recorded before `experiments` existed names no experiment, and its twin is
    `advised:strat_test:<strategy>` -- what every row before 2026-09-17 was tagged."""
    Path(advice.decision_path()).write_text(
        json.dumps(
            {
                "day": DAY,
                "params": {"iron_fly.profit_target_pct": 0.3},
                "reason": None,
                "experiment_id": "exp-2026-08-31-earnings-1",
            }
        ),
        encoding="utf-8",
    )
    decided = advice.decision(config(), DAY)
    twins = advice.twins_for(decided, "iron_fly")
    assert len(twins) == 1 and twins[0]["name"] is None
    twin = advice.twin_spec(SAVE_SPEC, twins[0]["params"], twins[0]["experiment_id"], name=twins[0]["name"])
    assert twin["profile"] == "advised:strat_test:iron_fly"
    assert twin["order_id"] == "advised-" + SAVE_SPEC["order_id"]
    assert twin["experiment_id"] == "exp-2026-08-31-earnings-1"


def test_the_loop_manages_a_twin_in_either_tag_shape():
    from cherrypick.earnings import paper_loop

    assert paper_loop.managed_book("advised:fly-target-early:iron_fly") is True
    assert paper_loop.managed_book("advised:strat_test:iron_fly") is True
    assert paper_loop.managed_book("strat_test:iron_fly") is True
    assert paper_loop.managed_book("advised:control") is False  # names no strategy
    assert paper_loop.managed_book("live:iron_fly") is False
    assert advice.strategy_of("advised:fly-target-early:iron_fly") == "iron_fly"
    assert advice.strategy_of("advised:strat_test:iron_fly") == "iron_fly"
    assert advice.strategy_of("strat_test:iron_fly") is None


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
    twin = advice.twin_spec(SAVE_SPEC, {"profit_target_pct": 0.3}, experiment_id="exp-2026-08-31-earnings-1")
    assert twin["profile"] == "advised:strat_test:iron_fly"
    assert twin["order_id"].startswith("advised-")
    assert json.loads(twin["advice_params"]) == {"profit_target_pct": 0.3}
    assert twin["experiment_id"] == "exp-2026-08-31-earnings-1"
    assert "experiment_id" not in SAVE_SPEC  # the control row is nobody's experiment
    # Every fill field is the control's, byte for byte — the comparison isolates management.
    for field in (
        "legs_json",
        "entry_credit",
        "quantity",
        "capital_at_risk",
        "entry_cost",
        "entry_slippage",
        "entry_iv",
        "symbol",
        "expiration",
        "strategy",
    ):
        assert twin[field] == SAVE_SPEC[field]


def test_the_twin_tag_is_what_the_verdict_groups_on():
    assert advice.advised_book("strat_test:iron_fly") == "advised:strat_test:iron_fly"
    assert (
        advice.advised_book("strat_test:iron_fly", "Fly Target (early)")
        == "advised:fly-target-early:iron_fly"
    )
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
    for trade in (
        {"strategy": "iron_fly"},
        {"strategy": "iron_fly", "advice_params": None},
        {"strategy": "iron_fly", "advice_params": "{}"},
    ):
        assert (
            management.effective_config(trade, base) is base
            or management.effective_config(trade, base) == base
        )


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
        "legs_json": json.dumps(
            [
                {"symbol": "AAPL  260815C00200000", "action": "Sell to Open", "quantity": 1},
            ]
        ),
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


def test_frozen_params_govern_an_open_advised_trade_after_the_artifact_expires():
    """The exit-continuity rule, pinned (2026-09-12): an expired artifact admits no twins, but a
    twin opened earlier carries its params on the row and `effective_config` restates them at
    every later tick, so it is managed under its own terms until it closes."""
    import json

    from cherrypick.earnings import management

    expired = {
        "module": "earnings",
        "session": DAY,
        "expires_at": "2026-01-01T00:00:00+00:00",
        "proposals": [{"param": "iron_fly.profit_target_pct", "value": 0.3, "rationale": "r"}],
    }
    assert core_advice.validate(expired, BOUNDS, DAY)["ok"] is False

    trade = {"strategy": "iron_fly", "advice_params": json.dumps({"profit_target_pct": 0.3})}
    effective = management.effective_config(trade, config())
    assert effective["strategies"]["iron_fly"]["profit_target_pct"] == 0.3
    assert effective["strategies"]["iron_condor"]["profit_target_pct"] == 0.5, "the control is untouched"
