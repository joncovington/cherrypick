import asyncio
import sys
import types
from datetime import date

import pytest

from cherrypick.earnings import tt


class _FakeWatchlist:
    def __init__(self, group_name, name, entries):
        self.group_name = group_name
        self.name = name
        self.watchlist_entries = entries


# --- pure helpers ------------------------------------------------------------


def test_num_converts_valid_values():
    assert tt._num("1.5") == 1.5
    assert tt._num(3) == 3.0


def test_num_none_and_invalid_return_none():
    assert tt._num(None) is None
    assert tt._num("not-a-number") is None


def test_error_flags_retryable_for_5xx():
    exc = RuntimeError("upstream returned 503 ")
    result = tt._error(exc)
    assert result["ok"] is False
    assert result.get("retryable") is True


def test_error_not_retryable_for_generic_exception():
    result = tt._error(ValueError("bad input"))
    assert result["ok"] is False
    assert "retryable" not in result


def test_strike_converts_and_handles_bad_values():
    class _Opt:
        strike_price = "150.5"

    assert tt._strike(_Opt()) == 150.5

    class _BadOpt:
        strike_price = None

    assert tt._strike(_BadOpt()) is None


def test_atm_window_centers_on_around_price():
    class _Opt:
        def __init__(self, strike):
            self.strike_price = strike

    options = [_Opt(s) for s in (90, 95, 100, 105, 110)]
    result = tt._atm_window(options, strike_count=1, around_price=100)
    strikes = sorted(o.strike_price for o in result)
    assert strikes == [95, 100, 105]


def test_atm_window_empty_options_returns_input():
    assert tt._atm_window([], strike_count=5, around_price=100) == []


def test_nearest_expiration_picks_closest_to_target_days():
    today = date.today()
    from datetime import timedelta

    expirations = [today + timedelta(days=d) for d in (1, 5, 30)]
    result = tt._nearest_expiration(expirations, target_days=0)
    assert result == expirations[0]


# --- watchlists --------------------------------------------------------------


def test_cmd_get_liquid_symbols_filters_to_the_liquidity_group_and_liquid_symbols_name(monkeypatch):
    """Mirrors scout's liquidity_service._default_fetch test -- same watchlist, same live-verified
    shape (group_name == "Liquidity" has multiple watchlists; only "Liquid Symbols" is wanted)."""
    watchlists = [
        _FakeWatchlist("Liquidity", "Liquid Symbols", [{"symbol": "aapl"}, {"symbol": "MSFT"}]),
        _FakeWatchlist("Liquidity", "High Options Volume", [{"symbol": "SPY"}]),
        _FakeWatchlist("tasty", "Market", [{"symbol": "IGNORED"}]),
    ]

    class _FakePublicWatchlist:
        @staticmethod
        async def get(_session):
            return watchlists

    fake_module = types.ModuleType("tastytrade.watchlists")
    fake_module.PublicWatchlist = _FakePublicWatchlist
    monkeypatch.setitem(sys.modules, "tastytrade.watchlists", fake_module)
    monkeypatch.setattr(tt, "get_session", lambda: object())

    result = asyncio.run(tt.cmd_get_liquid_symbols(None))
    assert result == {"ok": True, "symbols": ["AAPL", "MSFT"]}


def test_cmd_get_liquid_symbols_empty_watchlist_returns_empty_list(monkeypatch):
    class _FakePublicWatchlist:
        @staticmethod
        async def get(_session):
            return []

    fake_module = types.ModuleType("tastytrade.watchlists")
    fake_module.PublicWatchlist = _FakePublicWatchlist
    monkeypatch.setitem(sys.modules, "tastytrade.watchlists", fake_module)
    monkeypatch.setattr(tt, "get_session", lambda: object())

    result = asyncio.run(tt.cmd_get_liquid_symbols(None))
    assert result == {"ok": True, "symbols": []}


def test_cmd_get_liquid_symbols_degrades_on_exception(monkeypatch):
    def _boom():
        raise RuntimeError("no session")

    monkeypatch.setattr(tt, "get_session", _boom)
    result = asyncio.run(tt.cmd_get_liquid_symbols(None))
    assert result["ok"] is False


def test_cmd_get_watch_universe_unions_the_three_watchlists(monkeypatch):
    watchlists = [
        _FakeWatchlist("Liquidity", "Liquid Symbols", [{"symbol": "aapl"}, {"symbol": "MSFT"}]),
        _FakeWatchlist("Liquidity", "High Options Volume", [{"symbol": "SPY"}, {"symbol": "AAPL"}]),
        _FakeWatchlist("Earnings", "tasty Earnings", [{"symbol": "NET"}]),
        _FakeWatchlist("Earnings", "All Earnings", [{"symbol": "IGNORED_TOO_BROAD"}]),
        _FakeWatchlist("tasty", "Market", [{"symbol": "IGNORED"}]),
    ]

    class _FakePublicWatchlist:
        @staticmethod
        async def get(_session):
            return watchlists

    fake_module = types.ModuleType("tastytrade.watchlists")
    fake_module.PublicWatchlist = _FakePublicWatchlist
    monkeypatch.setitem(sys.modules, "tastytrade.watchlists", fake_module)
    monkeypatch.setattr(tt, "get_session", lambda: object())

    result = asyncio.run(tt.cmd_get_watch_universe(None))
    assert result == {"ok": True, "symbols": ["AAPL", "MSFT", "NET", "SPY"]}


def test_cmd_get_watch_universe_tolerates_a_missing_watchlist(monkeypatch):
    """Only "Liquid Symbols" is present -- "High Options Volume" and "tasty Earnings" missing
    entirely (e.g. renamed/retired) must not fail the whole union."""
    watchlists = [_FakeWatchlist("Liquidity", "Liquid Symbols", [{"symbol": "AAPL"}])]

    class _FakePublicWatchlist:
        @staticmethod
        async def get(_session):
            return watchlists

    fake_module = types.ModuleType("tastytrade.watchlists")
    fake_module.PublicWatchlist = _FakePublicWatchlist
    monkeypatch.setitem(sys.modules, "tastytrade.watchlists", fake_module)
    monkeypatch.setattr(tt, "get_session", lambda: object())

    result = asyncio.run(tt.cmd_get_watch_universe(None))
    assert result == {"ok": True, "symbols": ["AAPL"]}


def test_cmd_get_watch_universe_empty_when_none_present(monkeypatch):
    class _FakePublicWatchlist:
        @staticmethod
        async def get(_session):
            return []

    fake_module = types.ModuleType("tastytrade.watchlists")
    fake_module.PublicWatchlist = _FakePublicWatchlist
    monkeypatch.setitem(sys.modules, "tastytrade.watchlists", fake_module)
    monkeypatch.setattr(tt, "get_session", lambda: object())

    result = asyncio.run(tt.cmd_get_watch_universe(None))
    assert result == {"ok": True, "symbols": []}


def test_cmd_get_watch_universe_degrades_on_exception(monkeypatch):
    def _boom():
        raise RuntimeError("no session")

    monkeypatch.setattr(tt, "get_session", _boom)
    result = asyncio.run(tt.cmd_get_watch_universe(None))
    assert result["ok"] is False


def test_a_verb_outside_the_designation_reads_masks_the_account(monkeypatch, capsys):
    """Only the designation reads keep the full account number; every other verb's output is masked."""

    async def quoted(args):
        return {"ok": True, "account_number": "5WT01234"}

    monkeypatch.setattr(tt, "cmd_get_quote", quoted)
    monkeypatch.setattr(sys, "argv", ["tt", "get_quote", "--symbol", "AAPL"])
    tt.main()
    out = capsys.readouterr().out
    assert "5WT01234" not in out and "****1234" in out
    assert tt.FULL_ACCOUNT_VERBS == {"list_accounts", "get_account_info", "get_positions"}


def test_there_is_no_order_verb(monkeypatch):
    """Earnings placed its last live order before v0.12.0; the CLI is read-only now."""
    monkeypatch.setattr(sys, "argv", ["tt", "execute_trade", "--order", "{}"])
    with pytest.raises(SystemExit):
        tt.main()
