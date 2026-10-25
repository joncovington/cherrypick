"""`tt execute_trade` acts on the DESIGNATED account or refuses (2026-10-12).

It used to resolve through `core.broker.resolve_account`, whose last resort is the login's first
account: with the designation unset or the keyring unreadable, an order (or its dry-run preflight)
would have gone to whichever account the broker listed first. `get_account_info` keeps the fallback.
No broker or keyring is touched: `_get_account` is a fake recording what it was asked for, and the
designation is pinned per test (conftest pins a fake).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from cherrypick.earnings import tt

DESIGNATED = "TEST0001"
# Captured at collection, before conftest's autouse pin replaces it for each test.
_REAL_SEAM = getattr(tt, "_designated_account", lambda: None)

_OPEN = json.dumps(
    {
        "price": 1.0,
        "price_effect": "Credit",
        "legs": [
            {
                "symbol": "AAPL  261016C00250000",
                "instrument_type": "Equity Option",
                "action": "Sell to Open",
                "quantity": 1,
            }
        ],
    }
)


@pytest.fixture(autouse=True)
def _no_kill_gate(monkeypatch):
    """The order flow is under test here; the kill criteria gate has its own (test_kill_criteria.py)."""
    monkeypatch.setattr(tt, "kill_refusal", lambda *a, **k: None)


@pytest.fixture
def asked(monkeypatch):
    seen: list = []

    async def _get_account(account_number=None):
        seen.append(account_number)
        raise RuntimeError("stop here -- the account was resolved")

    monkeypatch.setattr(tt, "_get_account", _get_account)
    monkeypatch.setattr(tt, "_live_trading_enabled", lambda: True)
    monkeypatch.setattr(tt, "_load_config", lambda: {})
    return seen


@pytest.mark.parametrize("live", [False, True], ids=["dry run", "live"])
def test_execute_trade_refuses_without_a_designated_account(monkeypatch, asked, live):
    monkeypatch.setattr(tt, "_designated_account", lambda: None)
    args = SimpleNamespace(live=live, order=_OPEN, account_number=None, wait=0)
    result = asyncio.run(tt.cmd_execute_trade(args))
    assert result["ok"] is False
    assert "AccountNotDesignated" in result["error"] and "no designated account" in result["error"]
    assert asked == [], "an undesignated order must never resolve an account"


@pytest.mark.parametrize("live", [False, True], ids=["dry run", "live"])
def test_execute_trade_trades_the_designated_account(asked, live):
    asyncio.run(tt.cmd_execute_trade(SimpleNamespace(live=live, order=_OPEN, account_number=None, wait=0)))
    assert asked == [DESIGNATED]


def test_an_explicit_account_number_must_be_the_designated_one(asked):
    args = SimpleNamespace(live=True, order=_OPEN, account_number="OTHER9999", wait=0)
    result = asyncio.run(tt.cmd_execute_trade(args))
    assert result["ok"] is False and "AccountNotDesignated" in result["error"]
    assert "****9999" in result["error"] and "OTHER9999" not in result["error"]
    assert asked == []


def test_the_designated_number_passed_explicitly_is_accepted(asked):
    args = SimpleNamespace(live=True, order=_OPEN, account_number=DESIGNATED, wait=0)
    asyncio.run(tt.cmd_execute_trade(args))
    assert asked == [DESIGNATED]


def test_get_account_info_still_resolves_without_a_designation(monkeypatch, asked):
    monkeypatch.setattr(tt, "_designated_account", lambda: None)
    asyncio.run(tt.cmd_get_account_info(SimpleNamespace(account_number=None)))
    assert asked == [None]


def test_the_seam_reads_the_core_designation(monkeypatch):
    monkeypatch.setattr(tt._creds.store, "designated_account", lambda: "SEEN0042")
    assert _REAL_SEAM() == "SEEN0042"
