"""`tt execute_trade` / `adjust_order` name every leg by its OCC symbol (2026-10-12).

The agent builds the order from `get_strategies` legs or a ledger row, whose option key is the DXLink
streamer symbol (`.XSP261012P580`); tastytrade orders take OCC (`XSP   261012P00580000`). The command
used to pass whatever it was handed. Now a streamer symbol is converted, an OCC symbol passes through,
and a leg with no OCC form is refused before any account is read. No broker is touched: the account
is a fake that records the order it was asked to place.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from cherrypick.meic import tt

_SHORT_PUT_STREAMER = ".XSP261012P580"
_SHORT_PUT_OCC = "XSP   261012P00580000"
_LONG_PUT_STREAMER = ".XSP261012P577.5"
_LONG_PUT_OCC = "XSP   261012P00577500"


def _order(*symbols: str) -> str:
    actions = ("Sell to Open", "Buy to Open")
    return json.dumps(
        {
            "price": 0.40,
            "price_effect": "Credit",
            "order_type": "Limit",
            "time_in_force": "Day",
            "legs": [
                {"symbol": s, "instrument_type": "Equity Option", "action": actions[i % 2], "quantity": 1}
                for i, s in enumerate(symbols)
            ],
        }
    )


class _Preflight:
    errors: list = []
    warnings: list = []
    buying_power_effect = None


@pytest.fixture
def placed(monkeypatch):
    """The leg symbols of every order the fake account was asked to place or replace with."""
    seen: list[list[str]] = []

    class _Account:
        account_number = "TEST0001"

        async def place_order(self, session, order, dry_run):
            seen.append([leg.symbol for leg in order.legs])
            return _Preflight()

        async def replace_order(self, session, order_id, order, dry_run=True):
            seen.append([leg.symbol for leg in order.legs])
            return _Preflight()

    async def _get_account(account_number=None):
        return _Account()

    monkeypatch.setattr(tt, "_get_account", _get_account)
    monkeypatch.setattr(tt, "get_session", lambda: object())
    monkeypatch.setattr(tt, "_live_trading_enabled", lambda: True)
    monkeypatch.setattr(tt, "_load_config", lambda: {})
    return seen


def _execute(order: str) -> dict:
    return asyncio.run(tt.cmd_execute_trade(SimpleNamespace(dry_run=True, order=order, account_number=None)))


def test_streamer_symbols_reach_the_broker_as_occ(placed):
    result = _execute(_order(_SHORT_PUT_STREAMER, _LONG_PUT_STREAMER))
    assert result["ok"] is True
    assert placed == [[_SHORT_PUT_OCC, _LONG_PUT_OCC]]


def test_occ_symbols_pass_through_unchanged(placed):
    result = _execute(_order(_SHORT_PUT_OCC, _LONG_PUT_OCC))
    assert result["ok"] is True
    assert placed == [[_SHORT_PUT_OCC, _LONG_PUT_OCC]]


def test_a_mixed_order_is_all_occ(placed):
    _execute(_order(_SHORT_PUT_STREAMER, _LONG_PUT_OCC))
    assert placed == [[_SHORT_PUT_OCC, _LONG_PUT_OCC]]


@pytest.mark.parametrize("bad", ["XSP_C", "XSP", "", ".XSP261012X580", "XSP 261012P00580000"])
def test_a_leg_with_no_occ_form_is_refused_before_the_broker(placed, bad):
    result = _execute(_order(_SHORT_PUT_STREAMER, bad))
    assert result["ok"] is False
    assert result["reason"] == "leg_symbol_not_orderable"
    assert placed == [], "nothing may reach the broker when any leg cannot be named"


def test_a_leg_with_no_symbol_at_all_is_refused(placed):
    spec = json.loads(_order(_SHORT_PUT_STREAMER))
    del spec["legs"][0]["symbol"]
    result = _execute(json.dumps(spec))
    assert result["ok"] is False and result["reason"] == "leg_symbol_not_orderable"
    assert placed == []


def test_adjust_order_converts_its_legs_too(placed):
    args = SimpleNamespace(
        dry_run=True, order=_order(_SHORT_PUT_STREAMER, _LONG_PUT_STREAMER), order_id="1", account_number=None
    )
    asyncio.run(tt.cmd_adjust_order(args))
    assert placed == [[_SHORT_PUT_OCC, _LONG_PUT_OCC]]


def test_adjust_order_refuses_a_leg_with_no_occ_form(placed):
    args = SimpleNamespace(dry_run=True, order=_order("XSP_C"), order_id="1", account_number=None)
    result = asyncio.run(tt.cmd_adjust_order(args))
    assert result["ok"] is False and result["reason"] == "leg_symbol_not_orderable"
    assert placed == []
