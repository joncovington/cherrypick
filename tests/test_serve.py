"""dashboard --serve: generic section render-gating + an end-to-end handler spin-up.

Keeps build_model/_render_html pure (tested elsewhere); here we check the serve-only section gating and
that the live server returns the suite page and a /api/section/<id> payload — with the section module
subprocess stubbed so the test needs no cherrypick-gex checkout or streamer.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from cherrypick.orchestrator import dashboard, sections, serve

pytestmark = pytest.mark.unit

_SECTION = {"id": "gex", "title": "GEX", "endpoint": "/api/section/gex", "refresh": 15}
_MODEL = {
    "generated_at": "2026-07-11T00:00:00+00:00",
    "overall": "OK", "heartbeat_age_min": 1.0, "et_clock": "2026-07-11T09:40:00-04:00",
    "in_session": True, "is_trading_day": True, "notify_channels": ["log"],
    "active_findings": [], "suite": {}, "modules": [], "logs": [],
    "sections": [_SECTION],
}


# --- sections config helpers --------------------------------------------------------------------
def test_enabled_sections_and_by_id():
    assert sections.enabled_sections({}) == []
    cfg = {"dashboard": {"sections": [
        {"id": "gex", "enabled": True}, {"id": "pnl", "enabled": False}, {"enabled": True},
    ]}}
    assert [s["id"] for s in sections.enabled_sections(cfg)] == ["gex"]
    assert sections.by_id(cfg, "gex")["id"] == "gex"
    assert sections.by_id(cfg, "pnl") is None
    assert sections.refresh_seconds({"refresh_seconds": 30}) == 30 and sections.refresh_seconds({}) == 15


def test_fetch_missing_module_returns_not_ok(tmp_path):
    out = sections.fetch({"id": "gex", "path": str(tmp_path / "nope"), "fetch_argv": ["run.py"]})
    assert out["ok"] is False and "not found" in out["error"]


def test_argv_substitutes_params():
    argv = sections._argv({"fetch_argv": ["run.py", "section", "--symbol", "{symbol}"]}, {"symbol": "SPX"})
    assert argv == ["run.py", "section", "--symbol", "SPX"]


# --- render gating ------------------------------------------------------------------------------
def test_section_card_only_rendered_in_serve_mode():
    served = dashboard._render_html(_MODEL, serve=True)
    assert 'data-cp-section="gex"' in served and 'data-endpoint="/api/section/gex"' in served
    static = dashboard._render_html(_MODEL, serve=False)
    assert "cpsection" not in static and "data-cp-section" not in static


def test_no_sections_no_card():
    model = dict(_MODEL, sections=[])
    assert "cpsection" not in dashboard._render_html(model, serve=True)


# --- end-to-end handler -------------------------------------------------------------------------
def test_serve_handler_serves_page_and_section_api(monkeypatch):
    monkeypatch.setattr(dashboard, "build_model", lambda cfg: _MODEL)
    canned = {"ok": True, "title": "GEX — SPX", "subtitle": "245 strikes",
              "metrics": [{"label": "Net GEX", "value": "1K", "tone": "pos"}],
              "bars": {"labels": [600], "focus": 600, "series": [{"name": "OI", "values": [1000]}]}}
    monkeypatch.setattr(sections, "fetch", lambda sec, params: dict(canned, _params=params))

    cfg = {"dashboard": {"sections": [
        {"id": "gex", "title": "GEX", "enabled": True, "path": ".", "default_symbol": "SPX",
         "fetch_argv": ["run.py", "section", "--symbol", "{symbol}", "--json"]},
    ]}}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve._make_handler(cfg))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode()
        assert "Cherrypick — suite status" in page and 'data-cp-section="gex"' in page
        raw = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/section/gex?symbol=QQQ", timeout=5).read()
        payload = json.loads(raw)
        assert payload["ok"] is True and payload["_params"]["symbol"] == "QQQ"
        assert payload["metrics"][0]["label"] == "Net GEX"
        # unknown section id -> 404 with a json error body
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/section/nope", timeout=5)
        assert exc.value.code == 404 and json.loads(exc.value.read())["ok"] is False
    finally:
        httpd.shutdown()
        httpd.server_close()
