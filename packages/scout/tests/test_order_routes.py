import pytest
from fastapi.testclient import TestClient

from cherrypick.scout import config as _config
from cherrypick.scout.api import orders as _orders_api
from cherrypick.scout.app import create_app

PORT = 5057

_LEGS = [
    {"symbol": "AAPL  260116P00150000", "quantity": -1, "price": 2.00},
    {"symbol": "AAPL  260116P00145000", "quantity": 1, "price": 1.00},
]


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


def _csrf_headers(app):
    return {"Content-Type": "application/json", "X-Csrf-Token": app.state.csrf_token}


async def _fake_dry_run_ok(_broker_session, _legs):
    return {
        "ok": True,
        "dry_run": True,
        "account_number": "****1234",
        "buying_power": {"warnings": [], "change_in_buying_power": "-500"},
        "response": {},
    }


def test_partial_staged_renders(app_and_client):
    app, client = app_and_client
    resp = client.get("/partial/staged", headers=_headers(app))
    assert resp.status_code == 200
    assert 'id="staged-view"' in resp.text


def test_dry_run_route_requires_csrf(app_and_client):
    app, client = app_and_client
    resp = client.post(
        "/api/order/dry-run",
        headers=_headers(app, **{"Content-Type": "application/json"}),
        json={"legs": _LEGS},
    )
    assert resp.status_code == 403


def test_dry_run_route_returns_the_staging_result(app_and_client, monkeypatch):
    app, client = app_and_client
    monkeypatch.setattr(_orders_api._staging, "dry_run_order", _fake_dry_run_ok)
    resp = client.post(
        "/api/order/dry-run", headers=_headers(app, **_csrf_headers(app)), json={"legs": _LEGS}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["account_number"] == "****1234"


def test_dry_run_route_rejects_a_malformed_leg(app_and_client):
    app, client = app_and_client
    resp = client.post(
        "/api/order/dry-run",
        headers=_headers(app, **_csrf_headers(app)),
        json={"legs": [{"symbol": "X"}]},
    )
    assert resp.status_code == 422


def test_staged_starts_empty(app_and_client):
    _app, client = app_and_client
    resp = client.get("/api/staged", headers=_headers(_app))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "tickets": []}


def test_stage_save_list_delete_round_trips(app_and_client, monkeypatch):
    app, client = app_and_client
    monkeypatch.setattr(_orders_api._staging, "dry_run_order", _fake_dry_run_ok)

    resp = client.post(
        "/api/staged",
        headers=_headers(app, **_csrf_headers(app)),
        json={
            "symbol": "aapl",
            "strategy": "put_credit_spread",
            "legs": _LEGS,
            "credit": 100.0,
            "max_risk": 400.0,
            "note": "test",
        },
    )
    assert resp.status_code == 200
    ticket = resp.json()["ticket"]
    assert ticket["symbol"] == "AAPL"
    assert ticket["dry_run"]["ok"] is True

    resp = client.get("/api/staged", headers=_headers(app))
    tickets = resp.json()["tickets"]
    assert len(tickets) == 1
    assert tickets[0]["id"] == ticket["id"]

    resp = client.post(
        "/api/staged/delete", headers=_headers(app, **_csrf_headers(app)), json={"id": ticket["id"]}
    )
    assert resp.status_code == 200
    assert client.get("/api/staged", headers=_headers(app)).json()["tickets"] == []


def test_delete_unknown_ticket_returns_404(app_and_client):
    app, client = app_and_client
    resp = client.post("/api/staged/delete", headers=_headers(app, **_csrf_headers(app)), json={"id": "nope"})
    assert resp.status_code == 404


def test_stage_requires_csrf(app_and_client):
    app, client = app_and_client
    resp = client.post(
        "/api/staged",
        headers=_headers(app, **{"Content-Type": "application/json"}),
        json={"symbol": "AAPL", "legs": _LEGS},
    )
    assert resp.status_code == 403
