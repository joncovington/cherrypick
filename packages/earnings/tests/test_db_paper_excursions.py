"""`get_excursions` (docs/metrics-plan.md Phase 2): MAE/MFE per closed trade, mirrored from the
already-tracked max_unrealized_pnl/min_unrealized_pnl columns rather than derived from a raw mark
path -- this module already tracks the running best/worst on every usable mark (test_only_a_usable
_mark_moves_the_excursion, test_db_paper_lifecycle.py), so `get_excursions` only needs to expose
what `trades` already carries.
"""

import argparse
import json
import time

import pytest

from cherrypick.earnings import db_paper


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_paper, "DB_PATH", tmp_path / "paper_trades.db")
    db_paper.cmd_init_db(argparse.Namespace())


def _ns(**kwargs):
    return argparse.Namespace(**kwargs)


def _save(order_id="P1", strategy="iron_fly", profile="default"):
    db_paper.cmd_save_trade(
        _ns(
            data=json.dumps(
                {
                    "order_id": order_id,
                    "symbol": "AAPL",
                    "strategy": strategy,
                    "expiration": "2026-08-21",
                    "entry_credit": 2.0,
                    "legs_json": "[]",
                    "opened_at": time.time(),
                    "profile": profile,
                }
            )
        )
    )
    return order_id


def _mark(order_id, unrealized_pnl, usable=True):
    return db_paper.cmd_record_mark(
        _ns(data=json.dumps({"order_id": order_id, "unrealized_pnl": unrealized_pnl, "usable": usable}))
    )


def _close(order_id, pnl=10.0):
    return db_paper.cmd_save_close(_ns(data=json.dumps({"order_id": order_id, "pnl": pnl})))


def test_excursions_mirror_the_tracked_max_min_unrealized_pnl():
    _save()
    _mark("P1", 90.0)
    _mark("P1", -30.0)
    _mark("P1", 40.0)  # closed here
    _close("P1")

    out = db_paper.cmd_get_excursions(_ns(strategy=None, profile=None))
    assert out["ok"] is True
    assert len(out["positions"]) == 1
    p = out["positions"][0]
    assert p["order_id"] == "P1"
    assert p["mfe"] == 90.0
    assert p["mae"] == -30.0


def test_excursions_clamp_to_zero_when_never_underwater_or_never_ahead():
    _save("A")
    _mark("A", 10.0)
    _mark("A", 25.0)  # A was never underwater
    _close("A")

    _save("B")
    _mark("B", -5.0)
    _mark("B", -20.0)  # B was never ahead
    _close("B")

    out = db_paper.cmd_get_excursions(_ns(strategy=None, profile=None))
    by_id = {p["order_id"]: p for p in out["positions"]}
    assert by_id["A"]["mae"] == 0.0 and by_id["A"]["mfe"] == 25.0
    assert by_id["B"]["mae"] == -20.0 and by_id["B"]["mfe"] == 0.0


def test_only_usable_marks_move_the_reported_excursion():
    _save()
    _mark("P1", 50.0)
    _mark("P1", -9999.0, usable=False)  # a refused mark -- must not read as a real drawdown
    _close("P1")

    out = db_paper.cmd_get_excursions(_ns(strategy=None, profile=None))
    p = out["positions"][0]
    assert p["mae"] == 0.0  # never truly underwater once the refusal is excluded
    assert p["mfe"] == 50.0


def test_a_trade_never_marked_is_skipped_not_fabricated_as_zero():
    _save()
    _close("P1")  # closed without ever being marked -- pre-instrumentation-shaped case

    out = db_paper.cmd_get_excursions(_ns(strategy=None, profile=None))
    assert out["positions"] == []
    assert out["mae_distribution"] == {"median": None, "n": 0}
    assert out["mfe_distribution"] == {"median": None, "n": 0}


def test_excursions_filters_by_strategy_and_profile():
    _save("A", strategy="iron_fly", profile="control")
    _mark("A", 15.0)
    _close("A")
    _save("B", strategy="iron_condor", profile="strat_test")
    _mark("B", 25.0)
    _close("B")

    out = db_paper.cmd_get_excursions(_ns(strategy="iron_fly", profile=None))
    assert [p["order_id"] for p in out["positions"]] == ["A"]

    out2 = db_paper.cmd_get_excursions(_ns(strategy=None, profile="strat_test"))
    assert [p["order_id"] for p in out2["positions"]] == ["B"]


def test_excursions_open_trades_are_excluded():
    _save()
    _mark("P1", 999.0)  # never closed -- must not appear
    out = db_paper.cmd_get_excursions(_ns(strategy=None, profile=None))
    assert out["positions"] == []


def test_mfe_distribution_is_the_median_across_positions():
    _save("A")
    _mark("A", 10.0)
    _close("A")
    _save("B")
    _mark("B", 30.0)
    _close("B")

    out = db_paper.cmd_get_excursions(_ns(strategy=None, profile=None))
    assert out["mfe_distribution"] == {"median": 20.0, "n": 2}
