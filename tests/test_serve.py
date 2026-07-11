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

from cherrypick.orchestrator import dashboard, doctor, embeds, reconcile, sections, serve

pytestmark = pytest.mark.unit

_SECTION = {"id": "gex", "title": "GEX", "endpoint": "/api/section/gex", "refresh": 15}
_MODEL = {
    "generated_at": "2026-07-11T00:00:00+00:00",
    "overall": "OK",
    "heartbeat_age_min": 1.0,
    "et_clock": "2026-07-11T09:40:00-04:00",
    "in_session": True,
    "is_trading_day": True,
    "notify_channels": ["log"],
    "active_findings": [],
    "suite": {},
    "modules": [],
    "logs": [],
    "sections": [_SECTION],
    "embeds": [{"id": "meic", "title": "MEIC", "url": "/embed/meic", "kind": "server"}],
}


# --- sections config helpers --------------------------------------------------------------------
def test_enabled_sections_and_by_id():
    assert sections.enabled_sections({}) == []
    cfg = {
        "dashboard": {
            "sections": [
                {"id": "gex", "enabled": True},
                {"id": "pnl", "enabled": False},
                {"enabled": True},
            ]
        }
    }
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
    canned = {
        "ok": True,
        "title": "GEX — SPX",
        "subtitle": "245 strikes",
        "metrics": [{"label": "Net GEX", "value": "1K", "tone": "pos"}],
        "bars": {"labels": [600], "focus": 600, "series": [{"name": "OI", "values": [1000]}]},
    }
    monkeypatch.setattr(sections, "fetch", lambda sec, params: dict(canned, _params=params))

    cfg = {
        "dashboard": {
            "sections": [
                {
                    "id": "gex",
                    "title": "GEX",
                    "enabled": True,
                    "path": ".",
                    "default_symbol": "SPX",
                    "fetch_argv": ["run.py", "section", "--symbol", "{symbol}", "--json"],
                },
            ]
        }
    }
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve._make_handler(cfg))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode()
        assert "suite status" in page and 'data-cp-section="gex"' in page
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


def test_api_system_returns_doctor_checks(monkeypatch):
    monkeypatch.setattr(dashboard, "build_model", lambda cfg: _MODEL)
    checks = [
        doctor.Check("python", doctor.OK, "3.13"),
        doctor.Check("meic.streamer", doctor.WARN, "not running"),
    ]
    seen = {}
    monkeypatch.setattr(doctor, "run", lambda cfg, fast=False: seen.update(fast=fast) or checks)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve._make_handler({}))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        raw = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/system", timeout=5).read()
        payload = json.loads(raw)
        assert payload["ok"] is True
        assert payload["checks"] == [
            {"name": "python", "status": "OK", "detail": "3.13"},
            {"name": "meic.streamer", "status": "WARN", "detail": "not running"},
        ]
        # the live-checks card must poll doctor in fast mode (no authenticated broker round-trip)
        assert seen["fast"] is True
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_api_system_degrades_on_doctor_error(monkeypatch):
    monkeypatch.setattr(dashboard, "build_model", lambda cfg: _MODEL)

    def boom(cfg, fast=False):
        raise RuntimeError("broker unreachable")

    monkeypatch.setattr(doctor, "run", boom)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve._make_handler({}))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/system", timeout=5)
        assert resp.status == 200
        payload = json.loads(resp.read())
        assert payload["ok"] is False and "broker unreachable" in payload["error"]
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- embedded module dashboards -----------------------------------------------------------------
def test_embed_iframe_card_is_serve_only():
    served = dashboard._render_html(_MODEL, serve=True)
    assert 'class="card embed"' in served and 'src="/embed/meic"' in served
    static = dashboard._render_html(_MODEL, serve=False)
    assert "/embed/meic" not in static and "embedded module dashboards" not in static


def _serve_cfg():
    return {
        "dashboard": {
            "embeds": [
                {"id": "meic", "title": "MEIC", "enabled": True, "kind": "server", "port": 8801},
                {"id": "earn", "title": "Earnings", "enabled": True, "kind": "static", "output": "out.html"},
            ]
        }
    }


def test_embed_server_kind_redirects_to_module_port(monkeypatch):
    monkeypatch.setattr(
        embeds, "ensure_server", lambda e: {"ok": True, "running": True, "url": "http://127.0.0.1:8801/"}
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve._make_handler(_serve_cfg()))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        # do not auto-follow: assert the 302 to the module's own port
        req = urllib.request.Request(f"http://127.0.0.1:{port}/embed/meic")
        opener = urllib.request.build_opener(_NoRedirect())
        with pytest.raises(urllib.error.HTTPError) as exc:
            opener.open(req, timeout=5)
        assert exc.value.code == 302
        assert exc.value.headers["Location"] == "http://127.0.0.1:8801/"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_embed_static_kind_serves_generated_html(monkeypatch):
    monkeypatch.setattr(embeds, "build_static", lambda e: {"ok": True, "detail": "built"})
    monkeypatch.setattr(embeds, "read_static", lambda e: b"<html>earnings</html>")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve._make_handler(_serve_cfg()))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        body = urllib.request.urlopen(f"http://127.0.0.1:{port}/embed/earn", timeout=5).read()
        assert body == b"<html>earnings</html>"
        # unknown embed id -> 404
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/embed/nope", timeout=5)
        assert exc.value.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_embed_failure_renders_inline_never_500(monkeypatch):
    # a module build blowing up must degrade to an inline message, not crash the umbrella server
    def boom(e):
        raise RuntimeError("module exploded")

    monkeypatch.setattr(embeds, "build_static", boom)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve._make_handler(_serve_cfg()))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/embed/earn", timeout=5)
        assert resp.status == 200
        body = resp.read()
        assert b"unavailable" in body and b"module exploded" in body
    finally:
        httpd.shutdown()
        httpd.server_close()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None  # surface the 302 instead of following it


# --- reconcile (paper↔live isolation) -----------------------------------------------------------
def test_reconcile_card_is_serve_only():
    served = dashboard._render_html(_MODEL, serve=True)
    assert "paper↔live isolation" in served and "data-cp-reconcile" in served
    static = dashboard._render_html(_MODEL, serve=False)
    assert "data-cp-reconcile" not in static and "paper↔live isolation" not in static


def test_api_reconcile_returns_run_output(monkeypatch):
    monkeypatch.setattr(dashboard, "build_model", lambda cfg: _MODEL)
    result = {
        "ok": True,
        "verdict": "FLAT",
        "broker": {"reachable": True, "account": "****1234"},
        "paper": {},
    }
    monkeypatch.setattr(reconcile, "run", lambda cfg: result)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve._make_handler({}))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        raw = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/reconcile", timeout=5).read()
        payload = json.loads(raw)
        assert payload["verdict"] == "FLAT" and payload["broker"]["account"] == "****1234"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_api_reconcile_degrades_on_error(monkeypatch):
    monkeypatch.setattr(dashboard, "build_model", lambda cfg: _MODEL)

    def boom(cfg):
        raise RuntimeError("broker exploded")

    monkeypatch.setattr(reconcile, "run", boom)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve._make_handler({}))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/reconcile", timeout=5)
        assert resp.status == 200
        payload = json.loads(resp.read())
        assert payload["ok"] is False and "broker exploded" in payload["error"]
    finally:
        httpd.shutdown()
        httpd.server_close()
