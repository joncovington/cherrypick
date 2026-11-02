"""cherrypick.core.broker.rest_quotes and Broker.rest_quotes: the price path that does not go
through DXLink (2026-11-02). Offline; the SDK call is replaced by `fetch`."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from cherrypick.core import broker as _broker
from cherrypick.core import execution

NOW = datetime(2026, 11, 2, 19, 30, tzinfo=UTC)
P, C = "SPXW  261102P07565000", "SPXW  261102C07615000"


def _row(symbol, *, bid=None, ask=None, mid=None, last=None, age=5.0):
    return SimpleNamespace(
        symbol=symbol,
        bid=bid,
        ask=ask,
        mid=mid,
        last=last,
        mark=None,
        updated_at=datetime.fromtimestamp(NOW.timestamp() - age, UTC),
    )


def _quotes(rows, **kw):
    async def fetch(session, options=None, indices=None):
        return rows

    return asyncio.run(_broker.rest_quotes(object(), now_ts=NOW.timestamp(), fetch=fetch, **kw))


def test_rest_quotes_return_usable_option_quotes_and_index_spot():
    out = _quotes(
        [_row(P, bid=7.0, ask=7.3, mid=7.15), _row(C, bid=0.05, ask=0.10), _row("SPX", last=7549.76)],
        options=[P, C],
        indices=["SPX"],
    )
    assert out["options"][P] == {"bid": 7.0, "ask": 7.3, "mid": 7.15, "age_seconds": 5.0}
    assert out["options"][C]["mid"] == pytest.approx(0.075)
    assert out["indices"] == {"SPX": 7549.76}


def test_rest_quotes_leave_out_stale_crossed_and_askless_rows():
    """A broker-side feed that is itself stale says so in `updated_at`; an old price must not
    decide a stop, so the row is dropped, never degraded."""
    out = _quotes(
        [
            _row(P, bid=7.0, ask=7.3, age=61.0),
            _row(C, bid=0.20, ask=0.10),
            _row("SPXW  261102P07555000", bid=1.0, ask=None),
            _row("SPX", last=7549.76, age=120.0),
        ],
        options=[P, C, "SPXW  261102P07555000"],
        indices=["SPX"],
    )
    assert out == {"options": {}, "indices": {}}


def test_rest_quotes_ignore_symbols_nobody_asked_for():
    out = _quotes([_row("XSP   261102P00756000", bid=1.0, ask=1.1)], options=[P])
    assert out["options"] == {}


def test_rest_quotes_with_nothing_to_ask_make_no_call():
    async def fetch(session, options=None, indices=None):
        raise AssertionError("no call expected")

    assert asyncio.run(_broker.rest_quotes(object(), fetch=fetch)) == {"options": {}, "indices": {}}


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


def test_adapter_rest_quotes_fail_closed_on_an_error(monkeypatch):
    async def boom(session, **kw):
        raise ConnectionError("502 Bad Gateway")

    monkeypatch.setattr(_broker, "rest_quotes", boom)
    adapter = _adapter(monkeypatch)
    assert adapter.rest_quotes(options=[P], indices=["SPX"]) == {"options": {}, "indices": {}}


def test_adapter_rest_quotes_fail_closed_when_the_call_hangs(monkeypatch):
    """A hung read must not hang the tick that manages open positions."""

    async def hang(session, **kw):
        await asyncio.sleep(30)

    monkeypatch.setattr(_broker, "rest_quotes", hang)
    adapter = _adapter(monkeypatch)
    assert adapter.rest_quotes(options=[P], timeout_seconds=0.05) == {"options": {}, "indices": {}}
