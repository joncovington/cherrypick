import pytest
from fastapi.testclient import TestClient

from cherrypick.scout import config as _config
from cherrypick.scout.api import calendar as _calendar_api
from cherrypick.scout.app import create_app
from cherrypick.scout.services import watchlist as _watchlist

PORT = 5057


@pytest.fixture()
def app_and_client(managed_home, monkeypatch):
    cfg = _config.load()
    cfg["serve"]["port"] = PORT
    app = create_app(cfg)

    async def fake_get_calendar(_conn, _session, _cfg, _symbols, *, days=14, now=None):
        return {
            "ok": True,
            "as_of": 1000.0,
            "stale": False,
            "dolt_available": True,
            "days": days,
            "entries": [
                {
                    "symbol": "AAPL",
                    "date": "2027-01-20",
                    "when": "After market close",
                    "consensus_eps": 1.5,
                    "expected_move_pct": 0.08,
                    "iv_rank": "60",
                    "liquidity_rating": 4,
                    "source": "metrics",
                    "stale": False,
                }
            ],
        }

    monkeypatch.setattr(_calendar_api.calendar_service, "get_calendar", fake_get_calendar)
    with TestClient(app) as client:
        yield app, client


def _headers(app, **extra):
    headers = {"Host": f"127.0.0.1:{PORT}"}
    headers.update(extra)
    return headers


def test_api_calendar_returns_the_service_payload(app_and_client):
    app, client = app_and_client
    resp = client.get("/api/calendar", headers=_headers(app))
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["entries"][0]["symbol"] == "AAPL"


def test_api_calendar_passes_through_the_days_param(app_and_client):
    app, client = app_and_client
    resp = client.get("/api/calendar?days=30", headers=_headers(app))
    assert resp.json()["days"] == 30


def test_partial_calendar_renders_html_with_the_row(app_and_client):
    app, client = app_and_client
    resp = client.get("/partial/calendar", headers=_headers(app))
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "AAPL" in resp.text
    assert "8.0%" in resp.text


def test_partial_calendar_notes_when_dolt_is_unreachable(app_and_client, monkeypatch):
    app, client = app_and_client

    async def fake_get_calendar(*_a, **_kw):
        return {
            "ok": True,
            "as_of": 1000.0,
            "stale": True,
            "dolt_available": False,
            "days": 14,
            "entries": [],
        }

    monkeypatch.setattr(_calendar_api.calendar_service, "get_calendar", fake_get_calendar)
    resp = client.get("/partial/calendar", headers=_headers(app))
    assert "Dolt is unreachable" in resp.text
    assert "No earnings in this window" in resp.text


def test_calendar_reads_the_current_watchlist(app_and_client, monkeypatch):
    app, client = app_and_client
    seen_symbols = []

    async def fake_get_calendar(_conn, _session, _cfg, symbols, *, days=14, now=None):
        seen_symbols.append(list(symbols))
        return {
            "ok": True,
            "as_of": 1000.0,
            "stale": False,
            "dolt_available": True,
            "days": days,
            "entries": [],
        }

    monkeypatch.setattr(_calendar_api.calendar_service, "get_calendar", fake_get_calendar)
    _watchlist.save(app.state.watchlist_path, ["nvda", "amd"])
    client.get("/api/calendar", headers=_headers(app))
    assert seen_symbols == [["AMD", "NVDA"]]
