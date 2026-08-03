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
