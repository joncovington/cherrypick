"""The vendor's tiers, without the vendor: a tier without greeks fetches quotes and computes delta and
IV from the chain's own mids, never more requests are in flight than the tier allows, and what a tier
does not serve is refused before any request."""

from __future__ import annotations

import threading
import time
import types
from datetime import date, datetime

import pytest
from cherrypick.core import bs, chaindelta

from cherrypick.backtest import licensing, settings
from cherrypick.backtest.vendor import VendorRefusal
from cherrypick.backtest.vendor import thetadata as td

SPOT, VOL = 6000.0, 0.15
DAY = date(2026, 11, 18)
AT = datetime(2026, 11, 18, 13, 0)  # the vendor's naive stamp: ET wall clock, three hours to the close
YEARS = 3 * 3600 / chaindelta.YEAR_SECONDS


def _settings(subscription: str, max_concurrent: int = 4) -> dict:
    return settings.resolve(
        {"vendor": {"subscription": subscription, "max_concurrent_requests": max_concurrent}}
    )


class _Frame:  # the slice of a DataFrame the client reads; CI has no pandas
    def __init__(self, rows):
        self.rows = rows

    def itertuples(self, index=False):
        return (types.SimpleNamespace(**r) for r in self.rows)


def _quotes(*, expiration=DAY, underlying=None, strikes=range(5950, 6055, 5)):
    rows = []
    for k in strikes:
        for right in ("CALL", "PUT"):
            px = bs.price(SPOT, float(k), YEARS, VOL, right[0], 0.0)
            row = {"timestamp": AT, "expiration": expiration, "strike": float(k), "right": right,
                   "bid": max(0.0, px - 0.05), "ask": px + 0.05}  # fmt: skip
            if underlying is not None:
                row["underlying_price"] = underlying
            rows.append(row)
    return rows


def _client(subscription: str, answer, max_concurrent: int = 4):
    asked = []

    def call(method, **kw):
        asked.append(method)
        return answer(method, **kw)

    client = td.ThetaDataClient.__new__(td.ThetaDataClient)
    client.subscription = subscription
    client._call = call
    return client, asked


def test_each_tier_caps_the_requests_in_flight():
    assert settings.concurrency(_settings("value")) == 2
    assert settings.concurrency(_settings("value", 1)) == 1
    assert settings.concurrency(_settings("standard", 8)) == 4
    assert settings.concurrency(_settings("professional", 8)) == 8
    assert settings.TIERS.keys() == set(settings.SUBSCRIPTIONS)


def test_the_licence_status_says_what_the_tier_can_still_add():
    st = licensing.status(_settings("value"), today=date(2026, 11, 17))
    assert st["phase"] == "active"
    assert st["tier"] == {"name": "value", "greeks": False, "trades": False, "concurrent": 2,
                          "history_from": "2020-01-01"}  # fmt: skip
    assert licensing.status(_settings("standard"))["tier"]["greeks"] is True


def test_a_tier_with_greeks_asks_the_greeks_endpoint():
    client, asked = _client("standard", lambda m, **kw: _Frame([]))
    client.greeks(
        "SPXW", DAY.isoformat(), interval="1m", strike_range=10, start_time="09:30:00", end_time="16:00:00"
    )
    assert asked == [td.GREEKS_METHOD]


def test_a_tier_without_greeks_asks_for_quotes_and_computes_delta_from_the_mids():
    client, asked = _client("value", lambda m, **kw: _Frame(_quotes()))
    rows = client.greeks("SPXW", DAY.isoformat(), interval="1m", strike_range=10, start_time="09:30:00",
                         end_time="16:00:00")  # fmt: skip
    assert asked == [td.QUOTE_METHOD]
    atm_call = next(r for r in rows if r["strike"] == 6000.0 and r["right"] == "CALL")
    assert atm_call["delta"] == pytest.approx(bs.delta(SPOT, 6000.0, YEARS, VOL, "C", 0.0), abs=0.01)
    assert atm_call["iv"] == pytest.approx(VOL, abs=0.01)
    # A 0DTE chain's parity forward is its spot: the store's underlying, as on a live chain.
    assert atm_call["underlying"] == pytest.approx(SPOT, abs=0.05)


def test_the_vendors_own_underlying_wins_and_another_expiry_gets_none_never_a_carry_guess():
    own = td.quotes_with_greeks(_Frame(_quotes(underlying=6001.25)).itertuples(), expiration=DAY)
    assert {r.underlying_price for r in own.itertuples()} == {6001.25}
    later = td.quotes_with_greeks(
        _Frame(_quotes(expiration=date(2026, 12, 18))).itertuples(), expiration=None
    )
    assert {r.underlying_price for r in later.itertuples()} == {None}


def test_a_mid_no_vol_can_price_gets_no_delta_never_a_feeds_value():
    rows = _quotes()
    rows[0] = {**rows[0], "bid": 0.0, "ask": 0.0}  # no usable mid
    out = list(td.quotes_with_greeks(_Frame(rows).itertuples(), expiration=DAY).itertuples())
    assert out[0].delta is None and out[0].implied_vol is None
    assert out[1].delta is not None


def test_vix_minutes_refuse_quotes_without_the_vendors_own_underlying():
    client, _asked = _client("value", lambda m, **kw: _Frame(_quotes()))
    with pytest.raises(VendorRefusal) as refused:
        client.vix_minutes(DAY.isoformat(), "2026-12-16")
    assert refused.value.reason == "not_on_tier"
    client, _asked = _client("value", lambda m, **kw: _Frame(_quotes(underlying=17.5)))
    assert {v for _t, v in client.vix_minutes(DAY.isoformat(), "2026-12-16")} == {17.5}


def test_a_tier_without_prints_refuses_trades_before_any_request():
    client, asked = _client("value", lambda m, **kw: pytest.fail("no request may go out"))
    with pytest.raises(VendorRefusal) as refused:
        client.trades("SPY", DAY.isoformat())
    assert refused.value.reason == "not_on_tier" and asked == []


def test_no_more_requests_are_in_flight_than_the_tier_allows():
    client = td.ThetaDataClient.__new__(td.ThetaDataClient)
    client._gate = threading.BoundedSemaphore(settings.concurrency(_settings("value")))
    lock, state = threading.Lock(), {"now": 0, "most": 0}

    class Lib:
        def option_list_expirations(self, **kw):
            with lock:
                state["now"] += 1
                state["most"] = max(state["most"], state["now"])
            time.sleep(0.05)
            with lock:
                state["now"] -= 1
            return "answer"

    client._client = Lib()
    threads = [threading.Thread(target=client._call, args=("option_list_expirations",)) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state["most"] == 2
