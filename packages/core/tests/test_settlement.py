"""The share-leg arithmetic calendars and pmcc must agree on to the cent."""

from __future__ import annotations

import asyncio

import pytest

from cherrypick.core import settlement


def test_long_shares_earn_the_rise():
    assert settlement.share_pnl("long", 100, 600.0, 610.0) == pytest.approx(1000.0)


def test_long_shares_lose_the_fall():
    assert settlement.share_pnl("long", 100, 600.0, 590.0) == pytest.approx(-1000.0)


def test_short_shares_earn_the_fall():
    assert settlement.share_pnl("short", 100, 600.0, 590.0) == pytest.approx(1000.0)


def test_short_shares_lose_the_rise():
    assert settlement.share_pnl("short", 100, 600.0, 610.0) == pytest.approx(-1000.0)


def test_a_flat_disposal_is_zero_both_ways():
    assert settlement.share_pnl("long", 100, 600.0, 600.0) == 0.0
    assert settlement.share_pnl("short", 100, 600.0, 600.0) == 0.0


def test_it_is_booked_to_the_cent():
    """Rounded because it is booked, not intermediate -- calendars validates its derivation against
    the real books to the cent, so an unrounded value would read there as a validation failure."""
    assert settlement.share_pnl("long", 3, 100.0, 100.3333) == 1.0


def test_the_physical_decomposition_equals_the_raw_cash_flow():
    """Physical settlement is cash settlement PLUS a share leg, which is what lets one derivation
    and one validation serve both styles.

    Short put, strike K, credit E, settles at S_f, shares disposed at S_m:
        option leg  E - (K - S_f)
        share leg   S_m - S_f      (long shares, basis S_f)
        total       E - K + S_m    -- take E, buy at K, sell at S_m
    Basing the shares at K instead would double-count it.
    """
    K, E, S_f, S_m, shares = 600.0, 5.0, 590.0, 595.0, 100
    option_leg = (E - (K - S_f)) * shares
    share_leg = settlement.share_pnl("long", shares, S_f, S_m)
    assert option_leg + share_leg == pytest.approx((E - K + S_m) * shares)


# --------------------------------------------------------------------------- official settlement price
def test_official_index_close_prefers_tastytrade(monkeypatch):
    from tastytrade import market_data as md

    class Row:
        def __init__(self, close=None, last=None, mark=None):
            self.close, self.last, self.mark = close, last, mark

    async def fake_get_market_data_by_type(session, indices=None, **kw):
        assert indices == ["XSP"]
        return [Row(close=743.76)]

    monkeypatch.setattr(md, "get_market_data_by_type", fake_get_market_data_by_type)
    monkeypatch.setattr(settlement, "_yahoo_index_price", lambda s: (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr(settlement, "_barchart_index_price", lambda s: (_ for _ in ()).throw(AssertionError))

    price, source = asyncio.run(settlement.official_index_close(object(), "XSP"))
    # A posted CLOSE is the one genuinely-official reading -- tagged as such, and neither web
    # fallback is consulted (both raise if touched).
    assert price == 743.76 and source == "tastytrade_close"


def test_official_index_close_falls_back_to_last_or_mark_when_close_is_unset(monkeypatch):
    from tastytrade import market_data as md

    class Row:
        def __init__(self, close=None, last=None, mark=None):
            self.close, self.last, self.mark = close, last, mark

    async def fake_get_market_data_by_type(session, indices=None, **kw):
        return [Row(close=None, last=None, mark=743.76)]  # close not posted yet

    monkeypatch.setattr(md, "get_market_data_by_type", fake_get_market_data_by_type)
    # With no posted close, the web sources (whose post-close quote IS the closing print) are
    # preferred over tastytrade's intraday mark...
    monkeypatch.setattr(settlement, "_yahoo_index_price", lambda s: 744.10)
    price, source = asyncio.run(settlement.official_index_close(object(), "XSP"))
    assert price == 744.10 and source == "yahoo"


def test_official_index_close_marks_an_intraday_tick_provisional(monkeypatch):
    """...and if NOTHING authoritative answers, the intraday mark is still returned (better than
    no settlement at all) but tagged provisional, so `session_officially_settled` stays False and
    the loop keeps retrying. Before 2026-07-31 this was stamped 'official' and stopped the retry."""
    from tastytrade import market_data as md

    class Row:
        def __init__(self, close=None, last=None, mark=None):
            self.close, self.last, self.mark = close, last, mark

    async def fake_get_market_data_by_type(session, indices=None, **kw):
        return [Row(close=None, last=750.46, mark=None)]

    monkeypatch.setattr(md, "get_market_data_by_type", fake_get_market_data_by_type)
    monkeypatch.setattr(settlement, "_yahoo_index_price", lambda s: None)
    monkeypatch.setattr(settlement, "_barchart_index_price", lambda s: None)
    price, source = asyncio.run(settlement.official_index_close(object(), "XSP"))
    assert price == 750.46
    assert source == "tastytrade_last_provisional"
    assert not settlement.is_official_source(source)


def test_official_index_close_falls_back_to_yahoo_then_barchart(monkeypatch):
    from tastytrade import market_data as md

    async def empty(session, indices=None, **kw):
        return []

    monkeypatch.setattr(md, "get_market_data_by_type", empty)
    monkeypatch.setattr(settlement, "_yahoo_index_price", lambda s: 743.76)
    monkeypatch.setattr(settlement, "_barchart_index_price", lambda s: (_ for _ in ()).throw(AssertionError))
    price, source = asyncio.run(settlement.official_index_close(object(), "XSP"))
    assert price == 743.76 and source == "yahoo"

    monkeypatch.setattr(settlement, "_yahoo_index_price", lambda s: None)
    monkeypatch.setattr(settlement, "_barchart_index_price", lambda s: 743.76)
    price2, source2 = asyncio.run(settlement.official_index_close(object(), "XSP"))
    assert price2 == 743.76 and source2 == "barchart"


def test_official_index_close_reports_no_source_when_every_source_fails(monkeypatch):
    from tastytrade import market_data as md

    async def boom(session, indices=None, **kw):
        raise RuntimeError("network blip")

    monkeypatch.setattr(md, "get_market_data_by_type", boom)
    monkeypatch.setattr(settlement, "_yahoo_index_price", lambda s: None)
    monkeypatch.setattr(settlement, "_barchart_index_price", lambda s: None)
    price, reason = asyncio.run(settlement.official_index_close(object(), "XSP"))
    assert price is None and reason == "no_source_available"


def test_official_sources_is_the_posted_close_set():
    assert settlement.OFFICIAL_SOURCES == {"official", "tastytrade_close", "yahoo", "barchart"}
    assert settlement.is_official_source("yahoo") and not settlement.is_official_source(
        "tastytrade_last_provisional"
    )
    assert not settlement.is_official_source(None)
