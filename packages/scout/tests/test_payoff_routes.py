import json

import pytest
from fastapi.testclient import TestClient

from cherrypick.scout import config as _config
from cherrypick.scout.api import payoff as _payoff_api
from cherrypick.scout.app import create_app

PORT = 5057


@pytest.fixture()
def app_and_client(managed_home):
    cfg = _config.load()
    cfg["serve"]["port"] = PORT
    app = create_app(cfg)
    with TestClient(app) as client:
        yield app, client


def _headers(app, **extra):
    headers = {"Host": f"127.0.0.1:{PORT}"}
    headers.update(extra)
    return headers


_PUT_CREDIT_SPREAD = [
    {"kind": "put", "quantity": -1, "price": 2.00, "strike": 100},
    {"kind": "put", "quantity": 1, "price": 1.00, "strike": 95},
]


def test_payoff_route_computes_curve_and_breakevens(app_and_client):
    app, client = app_and_client
    resp = client.get(
        "/api/payoff",
        params={"legs": json.dumps(_PUT_CREDIT_SPREAD), "spot": 100},
        headers=_headers(app),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["breakevens"] == pytest.approx([99.0])
    assert body["max_profit"]["value"] == pytest.approx(100.0)
    assert body["pop"] is None  # no dte/iv supplied


def test_payoff_route_includes_pop_when_dte_and_iv_given(app_and_client, monkeypatch):
    app, client = app_and_client

    async def fake_rate(_conn, _session):
        return 0.05

    monkeypatch.setattr(_payoff_api.metrics_service, "get_risk_free_rate", fake_rate)
    resp = client.get(
        "/api/payoff",
        params={"legs": json.dumps(_PUT_CREDIT_SPREAD), "spot": 100, "dte": 30, "iv": 0.25},
        headers=_headers(app),
    )
    body = resp.json()
    assert body["pop"] is not None
    assert 0.0 <= body["pop"] <= 1.0
    assert body["expected_move"] is not None


def test_payoff_route_rejects_invalid_json(app_and_client):
    app, client = app_and_client
    resp = client.get("/api/payoff", params={"legs": "not json", "spot": 100}, headers=_headers(app))
    assert resp.status_code == 400


def test_payoff_route_rejects_an_empty_leg_list(app_and_client):
    app, client = app_and_client
    resp = client.get("/api/payoff", params={"legs": "[]", "spot": 100}, headers=_headers(app))
    assert resp.status_code == 400


def test_payoff_route_rejects_a_malformed_leg(app_and_client):
    app, client = app_and_client
    resp = client.get(
        "/api/payoff",
        params={"legs": json.dumps([{"kind": "call"}]), "spot": 100},
        headers=_headers(app),
    )
    assert resp.status_code == 400


def test_payoff_route_pop_degrades_gracefully_without_a_risk_free_rate(app_and_client, monkeypatch):
    app, client = app_and_client

    async def broken_rate(_conn, _session):
        raise RuntimeError("no credentials")

    monkeypatch.setattr(_payoff_api.metrics_service, "get_risk_free_rate", broken_rate)
    resp = client.get(
        "/api/payoff",
        params={"legs": json.dumps(_PUT_CREDIT_SPREAD), "spot": 100, "dte": 30, "iv": 0.25},
        headers=_headers(app),
    )
    assert resp.status_code == 200
    assert resp.json()["pop"] is not None  # still computed, using the r=0.0 fallback
