import sqlite3

import pytest
from fastapi.testclient import TestClient
from test_earnings_metrics_service import _make_db, _row

from cherrypick.scout import config as _config
from cherrypick.scout.api import earnings as _earnings_api
from cherrypick.scout.app import create_app
from cherrypick.scout.services import earnings_metrics_service

PORT = 5057


def test_fmt_market_cap_units_and_edge_cases():
    assert _earnings_api._fmt_market_cap(4_264_414_673.0) == "$4.26B"
    assert _earnings_api._fmt_market_cap(785_238_709.0) == "$785.24M"
    assert _earnings_api._fmt_market_cap(1_500_000_000_000.0) == "$1.50T"
    assert _earnings_api._fmt_market_cap(2_500.0) == "$2.50K"
    assert _earnings_api._fmt_market_cap(500.0) == "$500.00"
    assert _earnings_api._fmt_market_cap(-2_000_000_000.0) == "-$2.00B"
    assert _earnings_api._fmt_market_cap(None) == "—"
    assert _earnings_api._fmt_market_cap("not a number") == "—"


def test_fmt_pct2_two_decimal_places():
    assert _earnings_api._fmt_pct2(0.56921) == "56.92%"
    assert _earnings_api._fmt_pct2(0) == "0.00%"
    assert _earnings_api._fmt_pct2(None) == "—"


def test_fmt_price_formats_as_dollars():
    assert _earnings_api._fmt_price(283.07) == "$283.07"
    assert _earnings_api._fmt_price(1234.5) == "$1,234.50"
    assert _earnings_api._fmt_price(None) == "—"
    assert _earnings_api._fmt_price("not a number") == "—"


def test_fmt_volume_comma_grouped_whole_number():
    assert _earnings_api._fmt_volume(3_178_272) == "3,178,272"
    assert _earnings_api._fmt_volume(500) == "500"
    assert _earnings_api._fmt_volume(None) == "—"


def test_fmt_tier_recommended_has_no_title():
    html_out = _earnings_api._fmt_tier({"tier": "recommended", "tier_reasons": []})
    assert 'class="tier tier-recommended"' in html_out
    assert ">Recommended<" in html_out
    assert "title=" not in html_out


def test_fmt_tier_near_miss_and_fail_show_reasons_in_title():
    near_miss = _earnings_api._fmt_tier(
        {"tier": "near_miss", "tier_reasons": ["price $7.00 in near-miss band"]}
    )
    assert 'class="tier tier-near-miss"' in near_miss
    assert ">Near miss<" in near_miss
    assert 'title="price $7.00 in near-miss band"' in near_miss

    fail = _earnings_api._fmt_tier({"tier": "fail", "tier_reasons": ["term structure 0.01 above -0.004"]})
    assert 'class="tier tier-fail"' in fail
    assert ">Fail<" in fail


def test_fmt_tier_unscored_when_tier_is_none():
    html_out = _earnings_api._fmt_tier({"tier": None, "tier_reasons": ["no chain data"]})
    assert 'class="tier tier-unscored"' in html_out
    assert ">—<" in html_out
    assert 'title="no chain data"' in html_out


def test_fmt_tier_escapes_reasons():
    html_out = _earnings_api._fmt_tier({"tier": "fail", "tier_reasons": ["<script>alert(1)</script>"]})
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out


@pytest.fixture()
def app_and_client(managed_home, monkeypatch):
    cfg = _config.load()
    cfg["serve"]["port"] = PORT
    app = create_app(cfg)

    async def fake_get_upcoming(_conn, _session, _cfg, *, days=10):
        return {
            "ok": True,
            "entries": [
                {
                    "symbol": "AAPL",
                    "date": "2026-08-20",
                    "when": "After market close",
                    "iv_rank": "55",
                    "liquidity_rating": 4,
                    "price": 220.5,
                    "avg_volume": 45_000_000,
                    "tier": "recommended",
                    "tier_reasons": [],
                }
            ],
        }

    monkeypatch.setattr(_earnings_api.earnings_metrics_service, "get_upcoming", fake_get_upcoming)
    with TestClient(app) as client:
        yield app, client


def _headers(app, **extra):
    headers = {"Host": f"127.0.0.1:{PORT}"}
    headers.update(extra)
    return headers


def test_api_earnings_screens_returns_a_graceful_empty_result_with_no_db(
    app_and_client, monkeypatch, tmp_path
):
    app, client = app_and_client
    monkeypatch.setattr(earnings_metrics_service, "paper_db_path", lambda: tmp_path / "paper_trades.db")

    resp = client.get("/api/earnings-screens", headers=_headers(app))
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["rows"] == []


def test_api_earnings_screens_returns_populated_rows(app_and_client, monkeypatch, tmp_path):
    app, client = app_and_client
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(earnings_metrics_service, "paper_db_path", lambda: db_path)
    _make_db(db_path, [_row("2026-08-05", "AAPL", selected=1, composite_score=7.5, best_tier="A")])

    resp = client.get("/api/earnings-screens?date=2026-08-05&mode=paper", headers=_headers(app))
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["scan_date"] == "2026-08-05"
    assert body["rows"][0]["symbol"] == "AAPL"


def test_api_earnings_upcoming_returns_well_shaped_json(app_and_client):
    app, client = app_and_client
    resp = client.get("/api/earnings-upcoming?days=14", headers=_headers(app))
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["entries"][0]["symbol"] == "AAPL"


def test_partial_earnings_renders_200_with_no_db(app_and_client, monkeypatch, tmp_path):
    app, client = app_and_client
    monkeypatch.setattr(earnings_metrics_service, "paper_db_path", lambda: tmp_path / "paper_trades.db")

    resp = client.get("/partial/earnings", headers=_headers(app))
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "No entry reviews recorded" in resp.text or "no earnings database" in resp.text.lower()
    assert "AAPL" in resp.text  # from the stubbed upcoming calendar


def test_partial_earnings_renders_200_with_a_populated_db(app_and_client, monkeypatch, tmp_path):
    app, client = app_and_client
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(earnings_metrics_service, "paper_db_path", lambda: db_path)
    _make_db(
        db_path,
        [
            _row("2026-08-05", "AAPL", selected=1, composite_score=7.5, best_tier="A"),
            _row("2026-08-05", "GME", selected=0, composite_score=1.0, reason="low winrate"),
        ],
    )

    resp = client.get("/partial/earnings?mode=paper", headers=_headers(app))
    assert resp.status_code == 200
    assert 'class="accepted"' in resp.text
    assert 'class="rejected"' in resp.text
    assert "AAPL" in resp.text
    assert "GME" in resp.text


def test_partial_earnings_formats_iv_rv_and_term_structure_precision(app_and_client, monkeypatch, tmp_path):
    app, client = app_and_client
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(earnings_metrics_service, "paper_db_path", lambda: db_path)
    _make_db(
        db_path,
        [_row("2026-08-05", "AAPL", iv_rv_ratio=1.23456, term_structure=-0.0041234)],
    )

    resp = client.get("/partial/earnings?mode=paper", headers=_headers(app))
    assert resp.status_code == 200
    assert "1.2" in resp.text  # IV/RV rounded to 1 decimal
    assert "1.23456" not in resp.text
    assert "-0.004" in resp.text  # term structure rounded to 3 decimals
    assert "-0.0041234" not in resp.text


def test_partial_earnings_formats_iv_rank_percentile_and_market_cap(app_and_client, monkeypatch, tmp_path):
    app, client = app_and_client
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(earnings_metrics_service, "paper_db_path", lambda: db_path)
    _make_db(
        db_path,
        [_row("2026-08-05", "AAPL", iv_rank=0.56921, iv_percentile=0.4321, market_cap=4264414673.0)],
    )

    resp = client.get("/partial/earnings?mode=paper", headers=_headers(app))
    assert resp.status_code == 200
    assert "56.92%" in resp.text  # iv_rank, 2 decimal places
    assert "43.21%" in resp.text  # iv_percentile, 2 decimal places
    assert "$4.26B" in resp.text  # market cap, abbreviated dollar figure


def test_partial_earnings_upcoming_renders_tier_price_and_volume(app_and_client, monkeypatch, tmp_path):
    app, client = app_and_client
    monkeypatch.setattr(earnings_metrics_service, "paper_db_path", lambda: tmp_path / "paper_trades.db")

    resp = client.get("/partial/earnings", headers=_headers(app))
    assert resp.status_code == 200
    assert 'class="tier tier-recommended"' in resp.text
    assert ">Recommended<" in resp.text
    assert "$220.50" in resp.text  # price
    assert "45,000,000" in resp.text  # volume, comma-grouped


def test_partial_earnings_upcoming_header_has_thirteen_columns(app_and_client, monkeypatch, tmp_path):
    app, client = app_and_client
    monkeypatch.setattr(earnings_metrics_service, "paper_db_path", lambda: tmp_path / "paper_trades.db")

    resp = client.get("/partial/earnings", headers=_headers(app))
    assert resp.status_code == 200
    upcoming_section = resp.text.split('id="earnings-upcoming-view"')[1]
    header_row = upcoming_section.split("<thead>")[1].split("</thead>")[0]
    assert header_row.count("<th>") == 13
    for expected in (
        "Date",
        "Symbol",
        "Timing",
        "Tier",
        "Price",
        "Volume",
        "Winrate",
        "IV/RV",
        "Term structure",
        "Expected move",
        "Avg actual move",
        "IV rank",
        "Mkt cap",
    ):
        assert f"<th>{expected}</th>" in header_row


def test_partial_earnings_missing_entry_reviews_table_does_not_error(app_and_client, monkeypatch, tmp_path):
    app, client = app_and_client
    db_path = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE trades (order_id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(earnings_metrics_service, "paper_db_path", lambda: db_path)

    resp = client.get("/partial/earnings", headers=_headers(app))
    assert resp.status_code == 200


def test_partial_earnings_never_run_shows_notice_and_no_polling(app_and_client, monkeypatch):
    app, client = app_and_client

    async def fake_get_upcoming(*_a, **_kw):
        return {"ok": True, "entries": [], "watch": {"ok": True, "never_run": True, "total": 0, "done": 0}}

    monkeypatch.setattr(_earnings_api.earnings_metrics_service, "get_upcoming", fake_get_upcoming)

    resp = client.get("/partial/earnings", headers=_headers(app))
    assert resp.status_code == 200
    assert "haven't been computed yet" in resp.text
    assert 'hx-trigger="every 5s"' not in resp.text


def test_partial_earnings_in_progress_shows_progress_and_polls(app_and_client, monkeypatch):
    app, client = app_and_client

    async def fake_get_upcoming(*_a, **_kw):
        return {
            "ok": True,
            "entries": [],
            "watch": {
                "ok": True,
                "never_run": False,
                "pass_started_at": 100.0,
                "pass_completed_at": None,
                "total": 20,
                "done": 7,
            },
        }

    monkeypatch.setattr(_earnings_api.earnings_metrics_service, "get_upcoming", fake_get_upcoming)

    resp = client.get("/partial/earnings", headers=_headers(app))
    assert resp.status_code == 200
    assert "7 of 20 symbols done" in resp.text
    assert 'hx-trigger="every 5s"' in resp.text
    assert 'hx-get="/partial/earnings"' in resp.text


def test_partial_earnings_completed_pass_shows_timestamp_and_no_polling(app_and_client, monkeypatch):
    app, client = app_and_client

    async def fake_get_upcoming(*_a, **_kw):
        return {
            "ok": True,
            "entries": [],
            "watch": {
                "ok": True,
                "never_run": False,
                "pass_started_at": 100.0,
                "pass_completed_at": 200.0,
                "total": 5,
                "done": 5,
            },
        }

    monkeypatch.setattr(_earnings_api.earnings_metrics_service, "get_upcoming", fake_get_upcoming)

    resp = client.get("/partial/earnings", headers=_headers(app))
    assert resp.status_code == 200
    assert "last refreshed" in resp.text
    assert 'hx-trigger="every 5s"' not in resp.text
