"""The opposite-leg check is per broker account: loops on one account net at the broker, loops on
two never do -- a short in one account is no reason to refuse a long in another."""

from __future__ import annotations

import pytest

from cherrypick.core import livepositions as lp


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path / "home"))


LEG = {"underlying": "SPX", "expiry": "2026-11-20", "right": "P", "strike": 5000.0}


def _spec(action):
    return {"legs": [{"action": action, "symbol": "SPXW  261120P05000000", "quantity": 1}]}


def test_account_of():
    assert lp.account_of("meic") == "main" and lp.account_of("flies") == "main"
    assert lp.account_of("meic.margin") == "margin"


def test_loops_on_one_account_still_conflict():
    lp.publish_open_legs("flies", [{**LEG, "qty": -1}], now_iso="t")
    assert lp.cross_module_conflict("meic", "SPX", _spec("Buy to Open")) is not None


def test_a_loop_on_another_account_never_conflicts():
    lp.publish_open_legs("meic", [{**LEG, "qty": -1}], now_iso="t")
    assert lp.cross_module_conflict("meic.margin", "SPX", _spec("Buy to Open")) is None
    lp.publish_open_legs("meic.margin", [{**LEG, "qty": -1}], now_iso="t")
    assert lp.cross_module_conflict("flies", "SPX", _spec("Buy to Open")) is not None  # main's own short
    lp.publish_open_legs("meic", [], now_iso="t")
    assert lp.cross_module_conflict("flies", "SPX", _spec("Buy to Open")) is None  # margin's is not main's
