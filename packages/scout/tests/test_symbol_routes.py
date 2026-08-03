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


# --------------------------------------------------------------------------- /analysis (narrative)


def test_api_symbol_analysis_returns_trend_and_price_action(app_and_client, monkeypatch):
    # 60 flat bars then a 3-session surge: enough for the 1m trend params (20/26/30 SMAs) and a
    # big-move Price Action detection; 6m trend abstains (needs a 50-bar SMA -- present -- fine).
    closes = [100.0] * 60 + [100.0, 104.0, 107.0]
    bars = [
        {"t": 1000 + i * 86400, "o": c, "h": c + 1, "l": c - 1, "c": c, "v": 10}
        for i, c in enumerate(closes)
    ]

    async def fake_get_candles(_conn, _session, _cfg, symbol):
        return {"ok": True, "symbol": symbol.upper(), "as_of": 1000.0, "stale": False, "bars": bars}

    async def fake_get_metrics(_conn, _session, symbols, _ttl):
        return {s.upper(): {"earnings": None} for s in symbols}

    monkeypatch.setattr(_symbol_api.candle_service, "get_candles", fake_get_candles)
    monkeypatch.setattr(_symbol_api.metrics_service, "get_metrics", fake_get_metrics)

    app, client = app_and_client
    resp = client.get("/api/symbol/aapl/analysis", headers=_headers(app))
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["trend_1m"] is not None
    assert body["price_action"]  # always present when bars exist
    assert "AAPL" in body["price_action"]


def test_api_symbol_analysis_with_no_bars_degrades(app_and_client, monkeypatch):
    async def empty_candles(_conn, _session, _cfg, symbol):
        return {"ok": True, "symbol": symbol.upper(), "as_of": 1000.0, "stale": True, "bars": []}

    monkeypatch.setattr(_symbol_api.candle_service, "get_candles", empty_candles)
    app, client = app_and_client
    resp = client.get("/api/symbol/aapl/analysis", headers=_headers(app))
    body = resp.json()
    assert body["ok"] is True
    assert body["price_action"] is None
    assert body["headline"] is None


# --------------------------------------------------------------------------- /template (order editor)


def _fake_chain_expirations(strikes, expiration="2026-09-18"):
    options = []
    for strike in strikes:
        for option_type in ("C", "P"):
            options.append(
                {
                    "symbol": f"X {option_type}{strike}",
                    "streamer_symbol": f".X{option_type}{strike}",
                    "strike": float(strike),
                    "expiration": expiration,
                    "option_type": option_type,
                }
            )
    return {"ok": True, "symbol": "AAPL", "as_of": 0, "stale": False, "expirations": {expiration: options}}


@pytest.fixture()
def template_route(app_and_client, monkeypatch):
    app, client = app_and_client
    strikes = [80, 85, 90, 95, 100, 105, 110, 115, 120]

    async def fake_get_expirations(_conn, _session, _cfg, symbol):
        return _fake_chain_expirations(strikes)

    import re

    def _parse(sym):
        m = re.search(r"([CP])(\d+(?:\.\d+)?)$", sym)
        return m.group(1), float(m.group(2))

    async def fake_get_quotes(_conn, _session, symbols, **_kw):
        out = {}
        for s in symbols:
            _t, strike = _parse(s)
            mid = max(0.10, 5.0 - abs(strike - 100.0) * 0.2)
            out[s] = {"bid": mid - 0.05, "ask": mid + 0.05, "mid": mid, "mark": mid}
        return out

    async def fake_get_greeks(_conn, _session, streamer_symbols, **_kw):
        out = {}
        for s in streamer_symbols:
            option_type, strike = _parse(s)
            call_delta = max(0.02, min(0.98, 0.5 + (100.0 - strike) / 40))
            delta = call_delta if option_type == "C" else -(1 - call_delta)
            out[s] = {"delta": delta, "gamma": 0.01, "theta": -0.02, "vega": 0.05, "iv": 0.3, "price": 1.0}
        return out

    monkeypatch.setattr(_symbol_api.chain_service, "get_expirations", fake_get_expirations)
    monkeypatch.setattr(_symbol_api.chain_service, "get_quotes", fake_get_quotes)
    monkeypatch.setattr(_symbol_api.chain_service, "get_greeks", fake_get_greeks)
    return app, client


def test_template_route_builds_an_iron_condor(template_route):
    app, client = template_route
    resp = client.get(
        "/api/symbol/aapl/template",
        params={"expiration": "2026-09-18", "spot": 100.0, "action": "build", "name": "iron_condor"},
        headers=_headers(app),
    )
    body = resp.json()
    assert body["ok"] is True
    assert len(body["legs"]) == 4
    assert {lg["kind"] for lg in body["legs"]} == {"call", "put"}


def test_template_route_flips_a_vertical(template_route):
    import json as _json

    app, client = template_route
    built = client.get(
        "/api/symbol/aapl/template",
        params={"expiration": "2026-09-18", "spot": 100.0, "action": "build", "name": "call_vertical_credit"},
        headers=_headers(app),
    ).json()["legs"]
    flipped = client.get(
        "/api/symbol/aapl/template",
        params={
            "expiration": "2026-09-18", "spot": 100.0, "action": "flip", "legs": _json.dumps(built),
        },
        headers=_headers(app),
    ).json()
    assert flipped["ok"] is True
    assert all(lg["kind"] == "put" for lg in flipped["legs"])


def test_template_route_rejects_unknown_names_and_actions(template_route):
    app, client = template_route
    resp = client.get(
        "/api/symbol/aapl/template",
        params={"expiration": "2026-09-18", "spot": 100.0, "action": "build", "name": "nope"},
        headers=_headers(app),
    )
    assert resp.status_code == 400
    resp = client.get(
        "/api/symbol/aapl/template",
        params={"expiration": "2026-09-18", "spot": 100.0, "action": "explode"},
        headers=_headers(app),
    )
    assert resp.status_code == 400


def test_template_route_reports_unsupported_shapes_softly(template_route, monkeypatch):
    app, client = template_route

    async def thin_expirations(_conn, _session, _cfg, symbol):
        return _fake_chain_expirations([100])

    monkeypatch.setattr(_symbol_api.chain_service, "get_expirations", thin_expirations)
    resp = client.get(
        "/api/symbol/aapl/template",
        params={"expiration": "2026-09-18", "spot": 100.0, "action": "build", "name": "iron_condor"},
        headers=_headers(app),
    )
    body = resp.json()
    assert body["ok"] is False
    assert body["legs"] is None
    assert "reason" in body


def test_suggestions_route_builds_three_cards_per_sentiment(template_route):
    app, client = template_route
    resp = client.get(
        "/api/symbol/aapl/suggestions",
        params={"expiration": "2026-09-18", "spot": 100.0, "sentiment": "high_iv", "iv": 0.3, "dte": 46},
        headers=_headers(app),
    )
    body = resp.json()
    assert body["ok"] is True
    names = [c["name"] for c in body["cards"]]
    assert names == ["put_vertical_credit", "short_strangle", "call_vertical_credit"]
    for card in body["cards"]:
        assert card["legs"]
        assert card["pop"] is not None
        assert 0.0 <= card["pop"] <= 1.0
    strangle = body["cards"][1]
    assert strangle["max_risk"]["unbounded"] is True  # honesty: a strangle's risk is unlimited
    assert strangle["annualized_return"] is None  # no max_risk denominator -> no return claim


def test_suggestions_route_defaults_to_the_next_monthly_30_plus_days_out(template_route, monkeypatch):
    """User's rule: with no expiration pinned, suggestions target the next standard monthly cycle
    at least 30 days out -- preferred over a NEARER weekly that also clears 30 days."""
    from datetime import UTC, datetime, timedelta

    today = datetime.now(tz=UTC).date()
    probe = today + timedelta(days=30)
    while not (probe.weekday() == 4 and 15 <= probe.day <= 21):
        probe += timedelta(days=1)
    monthly = probe.isoformat()
    weekly = (today + timedelta(days=31)).isoformat()
    if weekly == monthly:
        weekly = (today + timedelta(days=32)).isoformat()

    app, client = template_route
    strikes = [80, 85, 90, 95, 100, 105, 110, 115, 120]

    async def fake_get_expirations(_conn, _session, _cfg, symbol):
        both = _fake_chain_expirations(strikes, expiration=monthly)["expirations"][monthly]
        weekly_opts = _fake_chain_expirations(strikes, expiration=weekly)["expirations"][weekly]
        return {"ok": True, "symbol": "AAPL", "as_of": 0, "stale": False,
                "expirations": {weekly: weekly_opts, monthly: both}}

    monkeypatch.setattr(_symbol_api.chain_service, "get_expirations", fake_get_expirations)
    resp = client.get(
        "/api/symbol/aapl/suggestions",
        params={"spot": 100.0, "sentiment": "bullish"},
        headers=_headers(app),
    )
    body = resp.json()
    assert body["ok"] is True
    assert body["expiration"] == monthly  # the monthly wins even though the weekly is nearer
    assert body["cards"]


def test_suggestions_route_rejects_unknown_sentiment(template_route):
    app, client = template_route
    resp = client.get(
        "/api/symbol/aapl/suggestions",
        params={"expiration": "2026-09-18", "spot": 100.0, "sentiment": "confused"},
        headers=_headers(app),
    )
    assert resp.status_code == 400


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
