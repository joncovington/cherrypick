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
    # A defined-risk put credit spread is flat on both tails -- lets the chart extrapolate past
    # the outermost strike instead of drawing a naked diagonal across the whole visible window.
    assert body["slope_below"] == 0
    assert body["slope_above"] == 0


def test_payoff_route_slopes_are_nonzero_for_an_uncapped_naked_leg(app_and_client):
    app, client = app_and_client
    naked_call = [{"kind": "call", "quantity": -1, "price": 2.0, "strike": 100}]
    resp = client.get(
        "/api/payoff", params={"legs": json.dumps(naked_call), "spot": 100}, headers=_headers(app)
    )
    body = resp.json()
    assert body["slope_below"] == 0  # put/call... a naked short call is worthless below its strike
    assert body["slope_above"] == -100  # short 1 contract -- loses $100 per $1 the stock rises


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


def test_payoff_route_returns_annualized_and_pow_and_texts(app_and_client, monkeypatch):
    app, client = app_and_client

    async def fake_rate(_conn, _session):
        return 0.05

    monkeypatch.setattr(_payoff_api.metrics_service, "get_risk_free_rate", fake_rate)
    single_short_put = [{"kind": "put", "quantity": -1, "price": 1.50, "strike": 95}]
    resp = client.get(
        "/api/payoff",
        params={
            "legs": json.dumps(single_short_put),
            "spot": 100,
            "dte": 25,
            "iv": 0.30,
            "symbol": "aapl",
            "expiration": "2026-08-28",
        },
        headers=_headers(app),
    )
    body = resp.json()
    assert body["raw_return"] == pytest.approx(150 / 9350, rel=1e-3)
    assert body["annualized_return"] > body["raw_return"]  # compounded over 25 days
    assert 0.5 < body["pow"] < 1.0
    assert body["model_greeks"]["delta"] > 0  # short put is long delta
    assert "bullish strategy" in body["explanation"]
    assert "put on AAPL" in body["suggestion"]
    assert "Model greeks" in body["greeks_text"]


def test_payoff_route_omits_suggestion_for_multi_leg_positions(app_and_client):
    app, client = app_and_client
    resp = client.get(
        "/api/payoff",
        params={
            "legs": json.dumps(_PUT_CREDIT_SPREAD),
            "spot": 100,
            "symbol": "AAPL",
            "expiration": "2026-08-28",
        },
        headers=_headers(app),
    )
    body = resp.json()
    assert body["suggestion"] is None  # the wheel framing only fits a lone short put
    assert body["explanation"]  # but the generic explanation is always present


def test_payoff_route_income_checklist_for_a_lone_short_put(app_and_client, monkeypatch):
    app, client = app_and_client

    async def fake_rate(_conn, _session):
        return 0.05

    async def fake_metrics(_conn, _session, symbols, _ttl):
        return {s.upper(): {"earnings": None} for s in symbols}

    monkeypatch.setattr(_payoff_api.metrics_service, "get_risk_free_rate", fake_rate)
    monkeypatch.setattr(_payoff_api.metrics_service, "get_metrics", fake_metrics)
    legs = [{"kind": "put", "quantity": -1, "price": 1.50, "strike": 95, "bid": 1.45, "ask": 1.55}]
    resp = client.get(
        "/api/payoff",
        params={
            "legs": json.dumps(legs), "spot": 100, "dte": 25, "iv": 0.30,
            "symbol": "AAPL", "expiration": "2026-08-28",
        },
        headers=_headers(app),
    )
    checklist = resp.json()["checklist"]
    assert checklist["kind"] == "income"
    names = [i["name"] for i in checklist["items"]]
    assert names == ["Probability of worthless", "Annualized return", "Earnings date", "Spread & liquidity"]
    by_name = {i["name"]: i["status"] for i in checklist["items"]}
    assert by_name["Spread & liquidity"] == "warn"  # 0.10 spread on a 1.50 mid = 6.7% -> warn band


def test_payoff_route_projected_yield_for_a_covered_call(app_and_client, monkeypatch):
    app, client = app_and_client

    async def fake_rate(_conn, _session):
        return 0.05

    async def fake_metrics(_conn, _session, symbols, _ttl):
        return {s.upper(): {"earnings": None, "dividend_yield": 0.0736} for s in symbols}

    monkeypatch.setattr(_payoff_api.metrics_service, "get_risk_free_rate", fake_rate)
    monkeypatch.setattr(_payoff_api.metrics_service, "get_metrics", fake_metrics)
    legs = [
        {"kind": "stock", "quantity": 1, "price": 28.63, "strike": None},  # 1 contract = 100 shares
        {"kind": "call", "quantity": -1, "price": 0.735, "strike": 30, "bid": 0.70, "ask": 0.77},
    ]
    resp = client.get(
        "/api/payoff",
        params={
            "legs": json.dumps(legs), "spot": 28.63, "dte": 46, "iv": 0.30,
            "symbol": "KWEB", "expiration": "2026-09-18",
        },
        headers=_headers(app),
    )
    body = resp.json()
    assert body["dividend_yield"] == pytest.approx(0.0736)
    assert body["projected_yield_12m"] == pytest.approx(
        body["annualized_return"] + 0.0736, abs=1e-6
    )


def test_payoff_route_projected_yield_omitted_for_a_lone_short_put(app_and_client, monkeypatch):
    app, client = app_and_client

    async def fake_rate(_conn, _session):
        return 0.05

    async def fake_metrics(_conn, _session, symbols, _ttl):
        return {s.upper(): {"earnings": None, "dividend_yield": 0.0736} for s in symbols}

    monkeypatch.setattr(_payoff_api.metrics_service, "get_risk_free_rate", fake_rate)
    monkeypatch.setattr(_payoff_api.metrics_service, "get_metrics", fake_metrics)
    legs = [{"kind": "put", "quantity": -1, "price": 1.50, "strike": 95, "bid": 1.45, "ask": 1.55}]
    resp = client.get(
        "/api/payoff",
        params={
            "legs": json.dumps(legs), "spot": 100, "dte": 25, "iv": 0.30,
            "symbol": "AAPL", "expiration": "2026-08-28",
        },
        headers=_headers(app),
    )
    body = resp.json()
    assert body["projected_yield_12m"] is None  # not a covered-call shape


def test_payoff_route_includes_score_for_a_defined_risk_vertical(app_and_client, monkeypatch):
    app, client = app_and_client

    async def fake_rate(_conn, _session):
        return 0.05

    monkeypatch.setattr(_payoff_api.metrics_service, "get_risk_free_rate", fake_rate)
    # APD 290/280 put vertical, 2026-08-04: reward $310 / risk $690 / pop 58.24% -> score ~84.
    legs = [
        {"kind": "put", "quantity": -1, "price": 4.30, "strike": 290},
        {"kind": "put", "quantity": 1, "price": 1.20, "strike": 280},
    ]
    resp = client.get(
        "/api/payoff",
        params={"legs": json.dumps(legs), "spot": 292.38, "dte": 46, "iv": 0.30},
        headers=_headers(app),
    )
    body = resp.json()
    assert body["score"] is not None
    assert 0 < body["score"] < 300


def test_payoff_route_probable_risk_2sd_for_an_unbounded_short_strangle(app_and_client, monkeypatch):
    app, client = app_and_client

    async def fake_rate(_conn, _session):
        return 0.05

    monkeypatch.setattr(_payoff_api.metrics_service, "get_risk_free_rate", fake_rate)
    legs = [
        {"kind": "put", "quantity": -1, "price": 15.20, "strike": 360},
        {"kind": "call", "quantity": -1, "price": 18.95, "strike": 385},
    ]
    resp = client.get(
        "/api/payoff",
        params={"legs": json.dumps(legs), "spot": 370.91, "dte": 74, "iv": 0.3422},
        headers=_headers(app),
    )
    body = resp.json()
    assert body["max_loss"]["unbounded"] is True
    assert body["probable_risk_2sd"] == pytest.approx(6923.96, abs=1.0)
    # An undefined-risk basket now gets an ESTIMATED score, using probable_risk_2sd as the risk
    # figure in the same formula -- not a replica of the reference platform's own (unresolved)
    # undefined-risk number, clearly marked as such.
    assert body["score_is_estimated"] is True
    reward = 3415.0  # (15.20 + 18.95) * 100, the credit collected
    assert body["score"] == pytest.approx(
        100 * body["pop"] * (reward + 6923.96) / 6923.96, abs=0.5
    )


def test_payoff_route_probable_risk_2sd_omitted_for_defined_risk(app_and_client, monkeypatch):
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
    assert body["probable_risk_2sd"] is None


def test_payoff_route_directional_checklist_for_a_vertical(app_and_client, monkeypatch):
    app, client = app_and_client

    async def fake_metrics(_conn, _session, symbols, _ttl):
        return {s.upper(): {"earnings": {"expected_report_date": "2026-08-20"}} for s in symbols}

    monkeypatch.setattr(_payoff_api.metrics_service, "get_metrics", fake_metrics)
    legs = [
        {"kind": "put", "quantity": -1, "price": 2.0, "strike": 100, "bid": 1.9, "ask": 2.1},
        {"kind": "put", "quantity": 1, "price": 1.0, "strike": 95, "bid": 0.9, "ask": 1.1},
    ]
    resp = client.get(
        "/api/payoff",
        params={"legs": json.dumps(legs), "spot": 100, "symbol": "AAPL", "expiration": "2026-08-28"},
        headers=_headers(app),
    )
    checklist = resp.json()["checklist"]
    assert checklist["kind"] == "directional"
    by_name = {i["name"]: i["status"] for i in checklist["items"]}
    assert by_name["Earnings date"] == "warn"  # Aug 20 report lands inside the Aug 28 expiration
    # No cached candles for AAPL/SPX in this test home -> trend rows warn rather than guess.
    assert by_name["Stock trend"] == "warn"
    assert by_name["Market trend"] == "warn"
    # Combo: conservative credit = 1.9 - 1.1 = 0.8; generous = 2.1 - 0.9 = 1.2; spread 40% -> fail.
    assert by_name["Spread & liquidity"] == "fail"


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
