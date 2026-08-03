import pytest
from fastapi.testclient import TestClient

from cherrypick.scout import config as _config
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


def test_healthz(app_and_client):
    _app, client = app_and_client
    resp = client.get("/healthz", headers=_headers(_app))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_index_page_serves_csrf_token_baked_in(app_and_client):
    app, client = app_and_client
    resp = client.get("/", headers=_headers(app))
    assert resp.status_code == 200
    assert app.state.csrf_token in resp.text
    assert "__CSRF__" not in resp.text


def test_get_watchlist_starts_empty(app_and_client):
    _app, client = app_and_client
    resp = client.get("/api/watchlist", headers=_headers(_app))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "symbols": []}


def test_post_watchlist_add_requires_csrf(app_and_client):
    _app, client = app_and_client
    resp = client.post(
        "/api/watchlist",
        headers=_headers(_app, **{"Content-Type": "application/json"}),
        json={"action": "add", "symbols": ["AAPL"]},
    )
    assert resp.status_code == 403


def test_post_watchlist_add_and_get_round_trips(app_and_client):
    app, client = app_and_client
    resp = client.post(
        "/api/watchlist",
        headers=_headers(app, **{"Content-Type": "application/json", "X-Csrf-Token": app.state.csrf_token}),
        json={"action": "add", "symbols": ["msft", "aapl"]},
    )
    assert resp.status_code == 200
    assert resp.json()["symbols"] == ["AAPL", "MSFT"]

    resp = client.get("/api/watchlist", headers=_headers(app))
    assert resp.json()["symbols"] == ["AAPL", "MSFT"]


def test_post_watchlist_remove(app_and_client):
    app, client = app_and_client
    csrf_headers = {"Content-Type": "application/json", "X-Csrf-Token": app.state.csrf_token}
    client.post(
        "/api/watchlist",
        headers=_headers(app, **csrf_headers),
        json={"action": "add", "symbols": ["msft", "aapl"]},
    )
    resp = client.post(
        "/api/watchlist",
        headers=_headers(app, **csrf_headers),
        json={"action": "remove", "symbols": ["msft"]},
    )
    assert resp.json()["symbols"] == ["AAPL"]


def test_vendor_and_css_assets_are_served(app_and_client):
    _app, client = app_and_client
    resp = client.get("/static/vendor/htmx.min.js", headers=_headers(_app))
    assert resp.status_code == 200
    resp = client.get("/static/css/scout.css", headers=_headers(_app))
    assert resp.status_code == 200
