"""Settled cash for a live path's purchases (`cherrypick.core.live.cash`) and the broker read it uses
(`cherrypick.core.broker.cash_funds`)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from cherrypick.core import broker as core_broker
from cherrypick.core.live import cash


def funds(kind="Cash", withdraw=2_000.0, bp=4_000.0):
    return {"margin_or_cash": kind, "cash_balance": 5_000.0, "cash_available_to_withdraw": withdraw,
            "equity_buying_power": bp, "pending_cash": 0.0}  # fmt: skip


def test_a_margin_account_has_no_settled_cash_limit():
    out = cash.settled_cash(funds("Margin"), 900.0)
    assert out["readable"] and out["kind"] == "margin" and out["limit"] is None


def test_a_cash_account_spends_the_smaller_figure_less_its_own_unsettled_sales():
    assert cash.settled_cash(funds(withdraw=2_000.0, bp=4_000.0), 0.0)["limit"] == 2_000.0
    assert cash.settled_cash(funds(withdraw=3_210.55, bp=1_875.0), 0.0)["limit"] == 1_875.0
    out = cash.settled_cash(funds(withdraw=2_000.0, bp=4_000.0), 967.2)
    assert out["limit"] == 1_032.8 and out["unsettled_proceeds"] == 967.2


def test_the_limit_never_goes_below_zero():
    assert cash.settled_cash(funds(withdraw=500.0), 967.2)["limit"] == 0.0


@pytest.mark.parametrize(
    "read",
    [None, {}, funds(kind=None), funds(kind="Futures"), {"margin_or_cash": "Cash"}],
)
def test_what_cannot_be_read_spends_nothing(read):
    out = cash.settled_cash(read, 0.0)
    assert out["readable"] is False and out["limit"] == 0.0 and out["reason"].startswith("cash_unreadable")


def _account(kind, **balances):
    async def get_balances(session):
        return SimpleNamespace(**balances)

    return SimpleNamespace(margin_or_cash=kind, get_balances=get_balances)


def test_the_broker_read_returns_floats_and_the_kind():
    acct = _account("Cash", cash_balance="6400.0", cash_available_to_withdraw="3210.55",
                    equity_buying_power="1875.0", pending_cash="0.0")  # fmt: skip
    out = asyncio.run(core_broker.cash_funds(acct, "sess"))
    assert out == {"margin_or_cash": "Cash", "cash_balance": 6400.0, "cash_available_to_withdraw": 3210.55,
                   "equity_buying_power": 1875.0, "pending_cash": 0.0}  # fmt: skip


def test_a_missing_figure_raises_rather_than_reading_as_zero():
    acct = _account("Cash", cash_balance="1.0", equity_buying_power="1.0", pending_cash="0")
    with pytest.raises(ValueError, match="cash_available_to_withdraw"):
        asyncio.run(core_broker.cash_funds(acct, "sess"))
