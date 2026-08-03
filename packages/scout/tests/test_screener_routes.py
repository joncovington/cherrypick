import pytest
from fastapi.testclient import TestClient

from cherrypick.scout import config as _config
from cherrypick.scout.api import screener as _screener_api
from cherrypick.scout.app import create_app

PORT = 5057


@pytest.fixture()
def app_and_client(managed_home, monkeypatch):
    cfg = _config.load()
    cfg["serve"]["port"] = PORT
    app = create_app(cfg)

    async def fake_run_screener(_conn, _session, _cfg, _symbols, strategy, **_kw):
        return {
            "ok": True,
            "as_of": 1000.0,
            "strategy": strategy,
            "candidates": [
                {
                    "symbol": "AAPL",
                    "spot": 100.0,
                    "iv_rank": 0.5,
                    "liquidity_rating": 4,
                    "skew_edge": -0.5,
                    "legs": [{"kind": "put", "quantity": -1, "price": 2.0, "strike": 95}],
                    "credit": 190.0,
                    "max_risk": 310.0,
                    "breakevens": [93.1],
                    "dte": 33,
                    "expiration": "2027-03-19",
                    "strategy": strategy,
                    "pop": 0.6,
                    "pop_heuristic": 0.4,
                    "return_on_risk": 0.61,
                    "composite_score": 0.18,
                }
            ],
            "skipped": [],
        }

    monkeypatch.setattr(_screener_api.screener_service, "run_screener", fake_run_screener)
    with TestClient(app) as client:
        yield app, client


def _headers(app, **extra):
    headers = {"Host": f"127.0.0.1:{PORT}"}
    headers.update(extra)
    return headers


def test_api_screener_returns_ranked_candidates(app_and_client):
    app, client = app_and_client
    resp = client.get("/api/screener", headers=_headers(app))
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["candidates"][0]["symbol"] == "AAPL"


def test_api_screener_passes_through_the_strategy_param(app_and_client):
    app, client = app_and_client
    resp = client.get("/api/screener?strategy=covered_call", headers=_headers(app))
    assert resp.json()["strategy"] == "covered_call"


def test_partial_screener_renders_the_shell(app_and_client):
    app, client = app_and_client
    resp = client.get("/partial/screener", headers=_headers(app))
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert 'id="screener-table"' in resp.text
    assert 'data-strategy="put_credit_spread"' in resp.text
    assert 'data-filter="iv"' in resp.text  # the chip panel is present in the shell


def test_api_screener_parses_chip_filter_params(app_and_client, monkeypatch):
    app, client = app_and_client
    seen = {}

    async def capture_run_screener(_conn, _session, _cfg, _symbols, strategy, *, filters=None, **_kw):
        seen["filters"] = filters
        return {"ok": True, "as_of": 0, "strategy": strategy, "candidates": [], "skipped": []}

    monkeypatch.setattr(_screener_api.screener_service, "run_screener", capture_run_screener)
    resp = client.get(
        "/api/screener?strategy=short_put&iv=gte50&liquidity=somewhat,very&cap=large,mega",
        headers=_headers(app),
    )
    assert resp.status_code == 200
    assert seen["filters"] == {
        "iv": {"gte50"},
        "liquidity": {"somewhat", "very"},
        "cap": {"large", "mega"},
    }


def test_api_screener_with_no_filter_params_passes_empty_filters(app_and_client, monkeypatch):
    app, client = app_and_client
    seen = {}

    async def capture_run_screener(_conn, _session, _cfg, _symbols, strategy, *, filters=None, **_kw):
        seen["filters"] = filters
        return {"ok": True, "as_of": 0, "strategy": strategy, "candidates": [], "skipped": []}

    monkeypatch.setattr(_screener_api.screener_service, "run_screener", capture_run_screener)
    client.get("/api/screener", headers=_headers(app))
    assert seen["filters"] == {}


def test_api_screener_rejects_an_unknown_bucket(app_and_client):
    app, client = app_and_client
    resp = client.get("/api/screener?cap=gigantic", headers=_headers(app))
    assert resp.status_code == 400
    assert "gigantic" in resp.json()["detail"]
