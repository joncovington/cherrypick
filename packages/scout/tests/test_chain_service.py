from datetime import date

import pytest

from cherrypick.scout.services import cache as _cache
from cherrypick.scout.services import chain_service


@pytest.fixture()
def conn(tmp_path):
    db = _cache.open_db(tmp_path / "cache.db")
    yield db
    db.close()


class _FakeOptionType:
    def __init__(self, value):
        self.value = value


class _FakeOption:
    def __init__(self, symbol, strike, expiration, option_type):
        self.symbol = symbol
        self.strike_price = strike
        self.expiration_date = expiration
        self.option_type = _FakeOptionType(option_type)


class _FakeQuote:
    def __init__(self, symbol, bid, ask, mid, mark):
        self.symbol = symbol
        self.bid = bid
        self.ask = ask
        self.mid = mid
        self.mark = mark


@pytest.mark.asyncio
async def test_get_expirations_caches_across_calls(conn, monkeypatch):
    calls = []
    exp = date(2027, 1, 15)

    async def fake_get_option_chain(_session, symbol):
        calls.append(symbol)
        return {exp: [_FakeOption("AAPL  270115C00100000", 100.0, exp, "C")]}

    monkeypatch.setattr(chain_service, "_serialize_option", lambda o: {"symbol": o.symbol})

    import sys
    import types

    fake_module = types.ModuleType("tastytrade.instruments")
    fake_module.get_option_chain = fake_get_option_chain
    monkeypatch.setitem(sys.modules, "tastytrade.instruments", fake_module)

    class _FakeSession:
        async def call(self, fn, *args, **kwargs):
            return await fn(object(), *args, **kwargs)

    cfg = {"refresh": {"chain_ttl_seconds": 300}}
    first = await chain_service.get_expirations(conn, _FakeSession(), cfg, "aapl")
    second = await chain_service.get_expirations(conn, _FakeSession(), cfg, "aapl")

    assert calls == ["AAPL"]  # second call was a cache hit
    assert first["ok"] is True
    assert first["symbol"] == "AAPL"
    assert "2027-01-15" in first["expirations"]
    assert second["stale"] is False


@pytest.mark.asyncio
async def test_get_quotes_only_refetches_stale_or_missing_symbols(conn, monkeypatch):
    calls = []

    class _FakeSession:
        async def call(self, fn, **kwargs):
            calls.append(sorted(kwargs["options"]))
            return await fn(object(), **kwargs)

    async def fake_get_market_data_by_type(_session, options):
        return [_FakeQuote(sym, 1.0, 1.2, 1.1, 1.1) for sym in options]

    import sys
    import types

    fake_module = types.ModuleType("tastytrade.market_data")
    fake_module.get_market_data_by_type = fake_get_market_data_by_type
    monkeypatch.setitem(sys.modules, "tastytrade.market_data", fake_module)

    result = await chain_service.get_quotes(conn, _FakeSession(), ["OPT1", "OPT2"], ttl=60, now=1000.0)
    assert set(result.keys()) == {"OPT1", "OPT2"}
    assert result["OPT1"]["mid"] == 1.1

    # Within TTL: a repeated request for the same symbols must not refetch at all.
    result2 = await chain_service.get_quotes(conn, _FakeSession(), ["OPT1", "OPT2"], ttl=60, now=1010.0)
    assert result2 == result
    assert calls == [["OPT1", "OPT2"]]


@pytest.mark.asyncio
async def test_get_quotes_chunks_large_symbol_lists(conn, monkeypatch):
    chunks = []

    class _FakeSession:
        async def call(self, fn, **kwargs):
            chunks.append(sorted(kwargs["options"]))
            return await fn(object(), **kwargs)

    async def fake_get_market_data_by_type(_session, options):
        return [_FakeQuote(sym, 1.0, 1.0, 1.0, 1.0) for sym in options]

    import sys
    import types

    fake_module = types.ModuleType("tastytrade.market_data")
    fake_module.get_market_data_by_type = fake_get_market_data_by_type
    monkeypatch.setitem(sys.modules, "tastytrade.market_data", fake_module)

    symbols = [f"OPT{i}" for i in range(250)]
    result = await chain_service.get_quotes(conn, _FakeSession(), symbols, ttl=60, now=1000.0)
    assert len(result) == 250
    assert len(chunks) == 3  # 100 + 100 + 50


@pytest.mark.asyncio
async def test_get_quotes_on_empty_list_is_empty(conn):
    class _FakeSession:
        async def call(self, *_a, **_kw):
            raise AssertionError("should never be called for an empty symbol list")

    result = await chain_service.get_quotes(conn, _FakeSession(), [], now=1000.0)
    assert result == {}


# --------------------------------------------------------------------------- greeks


_GREEKS = {"delta": -0.32, "gamma": 0.02, "theta": -0.05, "vega": 0.11, "iv": 0.31, "price": 2.1}


@pytest.mark.asyncio
async def test_get_greeks_prefers_the_shared_stream_cache(conn, monkeypatch, tmp_path):
    from cherrypick.core import streamcache as _core_streamcache

    shared = _core_streamcache.connect(tmp_path / "stream_cache.db")
    import time as _time

    shared.execute(
        "INSERT INTO stream_greeks (symbol, delta, gamma, theta, vega, rho, iv, price, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)",
        (".AAPL260828P95", -0.32, 0.02, -0.05, 0.11, 0.31, 2.1, _time.time()),
    )
    shared.commit()
    shared.close()

    monkeypatch.setattr(chain_service._streamcache, "cache_path", lambda: tmp_path / "stream_cache.db")

    async def fail_dxlink(*_a, **_kw):
        raise AssertionError("must not open DXLink when the shared cache covers the symbol")

    monkeypatch.setattr(chain_service, "_dxlink_greeks", fail_dxlink)

    result = await chain_service.get_greeks(conn, object(), [".AAPL260828P95"])
    assert result[".AAPL260828P95"]["delta"] == pytest.approx(-0.32)


@pytest.mark.asyncio
async def test_get_greeks_falls_back_to_dxlink_and_caches(conn, monkeypatch, tmp_path):
    monkeypatch.setattr(chain_service._streamcache, "cache_path", lambda: tmp_path / "absent.db")
    calls = []

    async def fake_dxlink(_session, symbols):
        calls.append(sorted(symbols))
        return {sym: dict(_GREEKS) for sym in symbols}

    monkeypatch.setattr(chain_service, "_dxlink_greeks", fake_dxlink)

    first = await chain_service.get_greeks(conn, object(), [".X1", ".X2"], now=1000.0)
    assert set(first) == {".X1", ".X2"}
    # Within TTL: served from scout's own cache, no second subscription.
    second = await chain_service.get_greeks(conn, object(), [".X1", ".X2"], now=1030.0)
    assert second == first
    assert calls == [[".X1", ".X2"]]


@pytest.mark.asyncio
async def test_income_grid_picks_nearest_delta_strike_per_tier(conn, monkeypatch, tmp_path):
    """Buckets choose the nearest in-window expiration; tiers choose the strike whose |delta| is
    nearest 15/25/35 -- the reverse-engineered rule the module documents."""
    import time as _time
    from datetime import UTC, datetime, timedelta

    monkeypatch.setattr(chain_service._streamcache, "cache_path", lambda: tmp_path / "absent.db")
    now = _time.time()
    today = datetime.fromtimestamp(now, tz=UTC).date()
    exp_short = (today + timedelta(days=25)).isoformat()
    exp_far = (today + timedelta(days=200)).isoformat()  # outside every bucket

    strikes = [70, 80, 90, 95, 100]
    deltas = {70: -0.08, 80: -0.16, 90: -0.27, 95: -0.36, 100: -0.50}

    def _opt(strike):
        return {
            "symbol": f"OPT P{strike}",
            "streamer_symbol": f".OPT{strike}",
            "strike": float(strike),
            "expiration": exp_short,
            "option_type": "P",
        }

    async def fake_get_expirations(_conn, _session, _cfg, symbol):
        return {
            "ok": True,
            "symbol": symbol,
            "as_of": now,
            "stale": False,
            "expirations": {exp_short: [_opt(s) for s in strikes], exp_far: [_opt(100)]},
        }

    async def fake_get_greeks(_conn, _session, streamer_symbols, **_kw):
        return {
            s: {
                "delta": deltas[int(s.replace(".OPT", ""))],
                "gamma": 0,
                "theta": 0,
                "vega": 0,
                "iv": 0.30,
                "price": 1.0,
            }
            for s in streamer_symbols
        }

    async def fake_get_quotes(_conn, _session, symbols, **_kw):
        return {s: {"bid": 1.9, "ask": 2.1, "mid": 2.0, "mark": 2.0} for s in symbols}

    monkeypatch.setattr(chain_service, "get_expirations", fake_get_expirations)
    monkeypatch.setattr(chain_service, "get_greeks", fake_get_greeks)
    monkeypatch.setattr(chain_service, "get_quotes", fake_get_quotes)

    result = await chain_service.income_grid(conn, object(), {}, "OPT", spot=100.0, now=now)
    assert set(result["grid"]) == {"short"}  # the 200-day expiration fits no bucket
    tiers = result["grid"]["short"]["tiers"]
    assert tiers["conservative"]["strike"] == 80.0  # |Δ|=.16, nearest .15
    assert tiers["optimal"]["strike"] == 90.0  # .27 nearest .25
    assert tiers["aggressive"]["strike"] == 95.0  # .36 nearest .35
    cell = tiers["conservative"]
    assert cell["credit"] == pytest.approx(200.0)
    assert cell["annualized_return"] > cell["raw_return"]
    assert 0.0 < cell["pow"] < 1.0


@pytest.mark.asyncio
async def test_get_greeks_missing_symbols_are_absent_not_errors(conn, monkeypatch, tmp_path):
    monkeypatch.setattr(chain_service._streamcache, "cache_path", lambda: tmp_path / "absent.db")

    async def empty_dxlink(*_a, **_kw):
        return {}

    monkeypatch.setattr(chain_service, "_dxlink_greeks", empty_dxlink)
    result = await chain_service.get_greeks(conn, object(), [".NOPE"], now=1000.0)
    assert result == {}
