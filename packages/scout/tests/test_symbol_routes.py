import pytest
from fastapi.testclient import TestClient

from cherrypick.scout import config as _config
from cherrypick.scout.api import symbol as _symbol_api
from cherrypick.scout.app import create_app

PORT = 5057


@pytest.fixture()
def app_and_client(managed_home, monkeypatch):
    cfg = _config.load()
    cfg["serve"]["port"] = PORT
    app = create_app(cfg)

    async def fake_get_candles(_conn, _session, _cfg, symbol):
        return {
            "ok": True,
            "symbol": symbol.upper(),
            "period": "1d",
            "as_of": 1000.0,
            "stale": False,
            "bars": [
                {"t": 100, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},
                {"t": 200, "o": 1.5, "h": 2.5, "l": 1.0, "c": 2.0, "v": 20},
            ],
        }

    async def fake_get_metrics(_conn, _session, symbols, _ttl):
        return {s.upper(): {"iv_rank": "55", "liquidity_rating": 4, "beta": 1.2} for s in symbols}

    monkeypatch.setattr(_symbol_api.candle_service, "get_candles", fake_get_candles)
    monkeypatch.setattr(_symbol_api.metrics_service, "get_metrics", fake_get_metrics)
    with TestClient(app) as client:
        yield app, client


def _headers(app, **extra):
    headers = {"Host": f"127.0.0.1:{PORT}"}
    headers.update(extra)
    return headers


def test_api_symbol_candles(app_and_client):
    app, client = app_and_client
    resp = client.get("/api/symbol/aapl/candles", headers=_headers(app))
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert len(body["bars"]) == 2


def test_api_symbol_stats_combines_candles_and_metrics(app_and_client):
    app, client = app_and_client
    resp = client.get("/api/symbol/aapl/stats", headers=_headers(app))
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["last_close"] == 2.0
    assert body["change_pct"] == pytest.approx((2.0 - 1.5) / 1.5)
    assert body["week52_high"] == 2.5
    assert body["week52_low"] == 0.5
    assert body["avg_volume_30d"] == 15.0
    assert body["iv_rank"] == "55"
    assert body["liquidity_rating"] == 4


def test_api_symbol_stats_on_no_bars_is_all_nulls_not_an_error(app_and_client, monkeypatch):
    app, client = app_and_client

    async def empty_candles(_conn, _session, _cfg, symbol):
        return {
            "ok": True,
            "symbol": symbol.upper(),
            "period": "1d",
            "as_of": 1000.0,
            "stale": True,
            "bars": [],
        }

    monkeypatch.setattr(_symbol_api.candle_service, "get_candles", empty_candles)
    resp = client.get("/api/symbol/aapl/stats", headers=_headers(app))
    body = resp.json()
    assert body["ok"] is True
    assert body["last_close"] is None
    assert body["change_pct"] is None


def test_partial_symbol_renders_the_shell_with_the_symbol(app_and_client):
    app, client = app_and_client
    resp = client.get("/partial/symbol/aapl", headers=_headers(app))
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert 'data-symbol="AAPL"' in resp.text
    assert "AAPL" in resp.text


# --------------------------------------------------------------------------- /levels (S/R + SMAs)


def test_api_symbol_levels_finds_swings_and_nearest_labels(app_and_client, monkeypatch):
    # 15 flat-ish bars with one clear swing low (90 @ i=5) and one swing high (110 @ i=9);
    # last close 100 sits between them, so both nearest labels must resolve.
    bars = []
    for i in range(15):
        bars.append(
            {
                "t": 1000 + i * 86400,
                "o": 100,
                "h": 110 if i == 9 else 105,
                "l": 90 if i == 5 else 95,
                "c": 100.0,
                "v": 10,
            }
        )

    async def fake_get_candles(_conn, _session, _cfg, symbol):
        return {"ok": True, "symbol": symbol.upper(), "as_of": 1000.0, "stale": False, "bars": bars}

    monkeypatch.setattr(_symbol_api.candle_service, "get_candles", fake_get_candles)
    app, client = app_and_client
    resp = client.get("/api/symbol/aapl/levels", headers=_headers(app))
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    kinds = {(lv["kind"], lv["price"]) for lv in body["levels"]}
    assert ("support", 90.0) in kinds
    assert ("resistance", 110.0) in kinds
    assert body["nearest_support"]["price"] == 90.0
    assert body["nearest_resistance"]["price"] == 110.0
    assert set(body["smas"]) == {"sma20", "sma50", "sma200"}
    assert body["smas"]["sma20"] == []  # only 15 bars -- warmup Nones are omitted, never zero-filled


def test_api_symbol_levels_with_no_bars_degrades_to_empty(app_and_client, monkeypatch):
    async def empty_candles(_conn, _session, _cfg, symbol):
        return {"ok": True, "symbol": symbol.upper(), "as_of": 1000.0, "stale": True, "bars": []}

    monkeypatch.setattr(_symbol_api.candle_service, "get_candles", empty_candles)
    app, client = app_and_client
    resp = client.get("/api/symbol/aapl/levels", headers=_headers(app))
    body = resp.json()
    assert body["ok"] is True
    assert body["levels"] == []
    assert body["nearest_support"] is None
    assert body["nearest_resistance"] is None


# --------------------------------------------------------------------------- /quote (builder prefill)


def test_api_symbol_quote_combines_quote_and_metrics(app_and_client, monkeypatch):
    app, client = app_and_client

    async def fake_get_quotes(_session, symbols, **_kwargs):
        return {s.upper(): {"last": 307.35, "change_pct": 0.01} for s in symbols}

    async def fake_get_metrics(_conn, _session, symbols, _ttl):
        return {s.upper(): {"iv_rank": "55", "iv_30d": 0.27} for s in symbols}

    monkeypatch.setattr(_symbol_api.quote_service, "get_quotes", fake_get_quotes)
    monkeypatch.setattr(_symbol_api.metrics_service, "get_metrics", fake_get_metrics)

    resp = client.get("/api/symbol/aapl/quote", headers=_headers(app))
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["symbol"] == "AAPL"
    assert body["last"] == 307.35
    assert body["iv_30d"] == 0.27
    assert body["iv_rank"] == "55"
    assert body["stale"] is False


def test_api_symbol_quote_never_touches_candle_service(app_and_client, monkeypatch):
    """The whole point of this route: it must never route through the candle-history-backed path
    (candle_service's DXLink backfill), which is what made builder symbol selection slow."""
    app, client = app_and_client

    async def fail_get_candles(*_a, **_kw):
        raise AssertionError("the /quote route must never call candle_service")

    async def fake_get_quotes(_session, symbols, **_kwargs):
        return {s.upper(): {"last": 100.0} for s in symbols}

    async def fake_get_metrics(_conn, _session, symbols, _ttl):
        return {s.upper(): {"iv_30d": 0.3} for s in symbols}

    monkeypatch.setattr(_symbol_api.candle_service, "get_candles", fail_get_candles)
    monkeypatch.setattr(_symbol_api.quote_service, "get_quotes", fake_get_quotes)
    monkeypatch.setattr(_symbol_api.metrics_service, "get_metrics", fake_get_metrics)

    resp = client.get("/api/symbol/aapl/quote", headers=_headers(app))
    assert resp.status_code == 200
    assert resp.json()["last"] == 100.0


def test_api_symbol_quote_degrades_gracefully_with_no_data(app_and_client, monkeypatch):
    app, client = app_and_client

    async def empty_quotes(_session, _symbols, **_kwargs):
        return {}

    async def empty_metrics(_conn, _session, _symbols, _ttl):
        return {}

    monkeypatch.setattr(_symbol_api.quote_service, "get_quotes", empty_quotes)
    monkeypatch.setattr(_symbol_api.metrics_service, "get_metrics", empty_metrics)

    resp = client.get("/api/symbol/aapl/quote", headers=_headers(app))
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["last"] is None
    assert body["iv_30d"] is None
    assert body["stale"] is True
