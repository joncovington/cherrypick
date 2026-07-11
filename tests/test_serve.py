"""dashboard --serve: render gating for the GEX section + an end-to-end handler spin-up.

Keeps build_model/_render_html pure (tested elsewhere); here we check the serve-only GEX gating and
that the live server returns the suite page and a /api/gex payload — with the GEX module subprocess
stubbed so the test needs no cherrypick-gex checkout or streamer.
"""

import json
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from cherrypick.orchestrator import dashboard, gex, serve

pytestmark = pytest.mark.unit

_MODEL = {
    "generated_at": "2026-07-11T00:00:00+00:00",
    "overall": "OK", "heartbeat_age_min": 1.0, "et_clock": "2026-07-11T09:40:00-04:00",
    "in_session": True, "is_trading_day": True, "notify_channels": ["log"],
    "active_findings": [], "suite": {}, "modules": [], "logs": [],
    "gex": {"enabled": True, "symbol": "SPX", "refresh": 15},
}


# --- GEX config helpers -------------------------------------------------------------------------
def test_gex_helpers_defaults_and_enable():
    assert gex.is_enabled({}) is False
    assert gex.default_symbol({}) == "SPX"
    assert gex.refresh_seconds({}) == 15
    cfg = {"gex": {"enabled": True, "default_symbol": "spx", "refresh_seconds": 30}}
    assert gex.is_enabled(cfg) is True
    assert gex.default_symbol(cfg) == "SPX"
    assert gex.refresh_seconds(cfg) == 30


def test_fetch_disabled_returns_not_ok():
    out = gex.fetch({"gex": {"enabled": False}})
    assert out["ok"] is False and "not enabled" in out["error"]


# --- render gating ------------------------------------------------------------------------------
def test_gex_card_only_rendered_in_serve_mode():
    served = dashboard._render_html(_MODEL, serve=True)
    assert "gexcard" in served and "GEXCFG" in served and "/api/gex" in served
    static = dashboard._render_html(_MODEL, serve=False)
    assert "gexcard" not in static and "GEXCFG" not in static


def test_gex_card_omitted_when_disabled_even_in_serve_mode():
    model = dict(_MODEL, gex={"enabled": False, "symbol": "SPX", "refresh": 15})
    assert "gexcard" not in dashboard._render_html(model, serve=True)


# --- end-to-end handler -------------------------------------------------------------------------
def test_serve_handler_serves_page_and_gex_api(monkeypatch):
    monkeypatch.setattr(dashboard, "build_model", lambda cfg: _MODEL)
    canned = {"ok": True, "symbol": "SPX", "expiration": "2026-07-11", "underlying_price": 605.0,
              "series": [{"strike": 600, "net_gex": 1000, "net_gex_vol": 200, "abs_gex": 1000}],
              "totals": {"net_gex": 1000, "zero_gamma": 604.0, "call_wall": 610, "put_wall": 600}}
    monkeypatch.setattr(gex, "fetch", lambda cfg, sym: dict(canned, symbol=sym))

    cfg = {"gex": {"enabled": True, "default_symbol": "SPX"}, "dashboard": {}}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve._make_handler(cfg))
    port = httpd.server_address[1]
    import threading
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode()
        assert "Cherrypick — suite status" in page and "gexcard" in page
        raw = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/gex?symbol=QQQ", timeout=5).read()
        payload = json.loads(raw)
        assert payload["ok"] is True and payload["symbol"] == "QQQ"
        assert payload["totals"]["call_wall"] == 610
    finally:
        httpd.shutdown()
        httpd.server_close()
