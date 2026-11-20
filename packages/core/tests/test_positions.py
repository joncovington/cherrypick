"""The broker positions read: `core.broker.positions` over a fake account, and the adapter's
`Broker.positions` that fails closed to None. No tastytrade, no network."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest

from cherrypick.core import broker as _broker
from cherrypick.core import execution


def _row(symbol, qty, direction, instrument="Equity", underlying=None):
    return SimpleNamespace(
        symbol=symbol,
        instrument_type=instrument,
        underlying_symbol=underlying or symbol,
        quantity=Decimal(str(qty)),
        quantity_direction=direction,
    )


class _Acct:
    def __init__(self, rows):
        self._rows = rows

    async def get_positions(self, session):
        return self._rows


def test_positions_sign_the_quantity_by_direction_and_drop_flat_rows():
    acct = _Acct(
        [
            _row("AAA", 12, "Long"),
            _row("BBB", 3, "Short"),
            _row("CCC", 0, "Zero"),
            _row("AAA  261218C00050000", 2, "Long", "Equity Option", "AAA"),
        ]
    )
    out = asyncio.run(_broker.positions(acct, "sess"))
    assert [(p["symbol"], p["quantity"]) for p in out] == [
        ("AAA", 12.0),
        ("BBB", -3.0),
        ("AAA  261218C00050000", 2.0),
    ]
    assert out[2]["instrument_type"] == "Equity Option" and out[2]["underlying_symbol"] == "AAA"


def test_a_position_with_an_unreadable_direction_fails_the_whole_read():
    acct = _Acct([_row("AAA", 5, "Long"), _row("BBB", 5, "Sideways")])
    with pytest.raises(ValueError):
        asyncio.run(_broker.positions(acct, "sess"))


@pytest.fixture(autouse=True)
def _fresh_loop():
    execution.Broker._shared_loop = None
    yield
    if execution.Broker._shared_loop is not None:
        execution.Broker._shared_loop.close()
    execution.Broker._shared_loop = None


def _adapter(monkeypatch):
    async def resolve_ok(session, number):
        return object()

    monkeypatch.setattr(_broker, "resolve_account", resolve_ok)
    return execution.Broker(get_session=lambda: object(), designated_account=lambda: "****0000")


def test_the_adapter_returns_the_positions_it_read(monkeypatch):
    async def fake(account, session):
        return [{"symbol": "AAA", "quantity": 4.0}]

    monkeypatch.setattr(_broker, "positions", fake)
    assert _adapter(monkeypatch).positions() == [{"symbol": "AAA", "quantity": 4.0}]


def test_an_unreadable_read_is_none_never_an_empty_list_and_resets_the_session(monkeypatch):
    async def boom(account, session):
        raise RuntimeError("bound to a different event loop")

    monkeypatch.setattr(_broker, "positions", boom)
    adapter = _adapter(monkeypatch)
    adapter._session, adapter._account = object(), object()
    assert adapter.positions() is None
    assert adapter._session is None and adapter._account is None


def test_an_undesignated_account_reads_as_none(monkeypatch):
    adapter = execution.Broker(get_session=lambda: object(), designated_account=lambda: None)
    assert adapter.positions() is None
