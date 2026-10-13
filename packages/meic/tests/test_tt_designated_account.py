"""`tt`'s order and account-mutating commands act on the DESIGNATED account or refuse (2026-10-12).

They used to resolve through `core.broker.resolve_account`, whose last resort is the login's first
account: with the designation unset or the keyring unreadable, `execute_trade` (dry run or live),
`adjust_order` and `close_position` would have acted on whichever account the broker listed first.
Read-only commands keep the fallback. No broker or keyring is touched here: `_get_account` is a fake
that records what it was asked for, and the designation is pinned per test (conftest pins a fake).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from cherrypick.meic import tt

DESIGNATED = "TEST0001"
# Captured at collection, before conftest's autouse pin replaces it for each test.
_REAL_SEAM = getattr(tt, "_designated_account", lambda: None)

_OPEN = json.dumps(
    {
        "price": 1.0,
        "price_effect": "Credit",
        "legs": [
            {
                "symbol": "XSP   261012P00580000",
                "instrument_type": "Equity Option",
                "action": "Sell to Open",
                "quantity": 1,
            }
        ],
    }
)


@pytest.fixture
def asked(monkeypatch):
    """Every account number `_get_account` is asked for; it then stops the command."""
    seen: list = []

    async def _get_account(account_number=None):
        seen.append(account_number)
        raise RuntimeError("stop here -- the account was resolved")

    monkeypatch.setattr(tt, "_get_account", _get_account)
    monkeypatch.setattr(tt, "_live_trading_enabled", lambda: True)
    monkeypatch.setattr(tt, "_load_config", lambda: {})
    return seen


def _undesignated(monkeypatch):
    monkeypatch.setattr(tt, "_designated_account", lambda: None)


_MUTATING = {
    "execute_trade dry run": lambda: tt.cmd_execute_trade(
        SimpleNamespace(dry_run=True, order=_OPEN, account_number=None)
    ),
    "execute_trade live": lambda: tt.cmd_execute_trade(
        SimpleNamespace(dry_run=False, order=_OPEN, account_number=None)
    ),
    "adjust_order": lambda: tt.cmd_adjust_order(
        SimpleNamespace(dry_run=False, order=_OPEN, order_id="1", account_number=None)
    ),
    "close_position": lambda: tt.cmd_close_position(SimpleNamespace(order_id="1", account_number=None)),
}


@pytest.mark.parametrize("command", sorted(_MUTATING))
def test_a_mutating_command_refuses_without_a_designated_account(monkeypatch, asked, command):
    _undesignated(monkeypatch)
    result = asyncio.run(_MUTATING[command]())
    assert result["ok"] is False
    assert "AccountNotDesignated" in result["error"] and "no designated account" in result["error"]
    assert asked == [], "an undesignated order command must never resolve an account"


@pytest.mark.parametrize("command", sorted(_MUTATING))
def test_a_mutating_command_trades_the_designated_account(asked, command):
    asyncio.run(_MUTATING[command]())
    assert asked == [DESIGNATED]


def test_an_explicit_account_number_must_be_the_designated_one(monkeypatch, asked):
    """Otherwise typing a number would walk around the refusal -- and the error masks both."""
    args = SimpleNamespace(dry_run=False, order=_OPEN, account_number="OTHER9999")
    result = asyncio.run(tt.cmd_execute_trade(args))
    assert result["ok"] is False and "AccountNotDesignated" in result["error"]
    assert "****9999" in result["error"] and "****0001" in result["error"]
    assert "OTHER9999" not in result["error"] and DESIGNATED not in result["error"]
    assert asked == []


def test_the_designated_number_passed_explicitly_is_accepted(asked):
    """`live_smoke` passes `--account_number <designated>`; that keeps working."""
    args = SimpleNamespace(dry_run=True, order=_OPEN, account_number=DESIGNATED)
    asyncio.run(tt.cmd_execute_trade(args))
    assert asked == [DESIGNATED]


@pytest.mark.parametrize(
    "command",
    [tt.cmd_get_account_info, tt.cmd_get_positions, tt.cmd_get_working_orders],
    ids=["get_account_info", "get_positions", "get_working_orders"],
)
def test_read_only_commands_still_resolve_without_a_designation(monkeypatch, asked, command):
    _undesignated(monkeypatch)
    asyncio.run(command(SimpleNamespace(account_number=None)))
    assert asked == [None]


def test_the_seam_reads_the_core_designation(monkeypatch):
    """`_designated_account` is the credential store's one reading, not a second spelling of it."""
    monkeypatch.setattr(tt._creds.store, "designated_account", lambda: "SEEN0042")
    assert _REAL_SEAM() == "SEEN0042"
