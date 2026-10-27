"""The manual live door (`tt execute_trade --live`) obeys the loop's entry rules for an OPENING order:
the suite halt, today's arm record and the written kill criteria. A close always gets through."""

import asyncio
import json
from datetime import datetime

import pytest
from cherrypick.core import home as _home
from cherrypick.core import killcriteria as _kill
from cherrypick.core import live as _core_live

from cherrypick.meic import live_loop, tt

pytestmark = pytest.mark.usefixtures("real_manual_gate")

# Captured at import, before any fixture stubs it.
_REAL_SHOULD_DISARM = live_loop.should_disarm


def _order(action: str, quantity: int = 1) -> str:
    return json.dumps(
        {
            "price": 1.0,
            "price_effect": "credit",
            "legs": [
                {
                    "instrument_type": "Equity Option",
                    "symbol": "SPXW  261027P05800000",
                    "action": action,
                    "quantity": quantity,
                }
            ],
        }
    )


@pytest.fixture
def submit(monkeypatch):
    """Live enabled, armed for today, a normal kill verdict, and an account that records arrival."""
    reached = []
    monkeypatch.setattr(tt, "_live_trading_enabled", lambda: True)
    monkeypatch.setattr(tt, "_load_config", lambda: {"enable_live_trading": True, "live": {}})
    monkeypatch.setattr(live_loop, "should_disarm", lambda config, now_min, today: None)
    monkeypatch.setattr(live_loop, "kill_verdict", lambda config, db: {"state": _kill.NORMAL, "reasons": []})

    async def _account(account_number=None):
        reached.append(True)
        raise RuntimeError("stop at the account: nothing is placed in a test")

    monkeypatch.setattr(tt, "_get_account", _account)

    def run(action="Sell to Open", quantity=1):
        args = type(
            "Args", (), {"dry_run": False, "order": _order(action, quantity), "account_number": None}
        )()
        return asyncio.run(tt.cmd_execute_trade(args))

    return run, reached


def _halt():
    path = _home.halt_flag_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def test_an_armed_unhalted_open_reaches_the_broker(submit):
    run, reached = submit
    run()
    assert reached == [True]


def test_a_halt_refuses_an_open_and_lets_a_close_through(submit):
    run, reached = submit
    _halt()
    out = run("Sell to Open")
    assert out["ok"] is False and out["reason"] == "manual_open_refused" and "halt flag" in out["error"]
    assert reached == []
    run("Buy to Close")
    assert reached == [True]


def test_an_unarmed_day_refuses_an_open(submit, monkeypatch):
    run, reached = submit
    monkeypatch.setattr(live_loop, "should_disarm", lambda config, now_min, today: "no arm record for today")
    out = run()
    assert out["ok"] is False and "not armed" in out["error"] and reached == []


def test_a_blocked_kill_verdict_refuses_an_open(submit, monkeypatch):
    run, reached = submit
    blocked = {"state": _kill.BLOCKED, "reasons": ["drawdown past the block line"]}
    monkeypatch.setattr(live_loop, "kill_verdict", lambda config, db: blocked)
    out = run()
    assert out["ok"] is False and "drawdown past the block line" in out["error"] and reached == []


def test_a_reduced_verdict_refuses_an_order_wider_than_the_loop_would_enter(submit, monkeypatch):
    run, reached = submit
    reduced = {"state": _kill.REDUCED, "reasons": ["streak at the reduce line"]}
    monkeypatch.setattr(live_loop, "kill_verdict", lambda config, db: reduced)
    out = run(quantity=4)  # halved: the loop would enter 2
    assert out["ok"] is False and "2 contract(s)" in out["error"] and reached == []
    assert run(quantity=2)["ok"] is False and reached == []  # halved again to 1
    run(quantity=1)  # one is the floor: it goes through
    assert reached == [True]


def test_a_hand_written_limit_caps_a_normal_verdict_at_one(submit, monkeypatch):
    """The loop sizes to one under a hand-written limit whatever the state; the manual door matches."""
    run, reached = submit
    monkeypatch.setattr(
        tt,
        "_load_config",
        lambda: {"enable_live_trading": True, "live": {"kill": {"hand_limit_dollars": 500}}},
    )
    assert run(quantity=2)["ok"] is False and reached == []
    run(quantity=1)
    assert reached == [True]


def test_the_real_arm_record_is_what_arms_the_manual_door(submit, monkeypatch):
    """Not a stub: an absent record refuses, today's record admits. (Never `monkeypatch.undo()` here:
    it would also undo the managed home and write the record into the real one.)"""
    run, reached = submit
    monkeypatch.setattr(live_loop, "should_disarm", _REAL_SHOULD_DISARM)
    monkeypatch.setattr(
        tt, "_load_config", lambda: {"enable_live_trading": True, "live": {"disarm_time": "23:59"}}
    )
    assert "not armed" in run()["error"] and reached == []
    today = datetime.now(tt._ET).date().isoformat()
    _core_live.write_arm_record("meic", date=today, armed_by="test", at=f"{today}T09:00:00")
    run()
    assert reached == [True]
