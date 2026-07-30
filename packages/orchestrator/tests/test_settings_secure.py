"""settings_serve: the request gate on the suite's one mutating HTTP surface.

Spins the real handler on an ephemeral loopback port (same technique as test_serve.py) and asserts
the gate: bad Host → 403 everywhere; POSTs additionally need the session CSRF token, a JSON content
type, and a local Origin; no response ever carries a CORS header; a valid POST reaches the dispatch
and a guarded config pointer is still refused there. Also: a non-loopback bind is refused outright.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import settings_serve

pytestmark = pytest.mark.unit

TOKEN = "test-csrf-token"


@pytest.fixture
def server(tmp_path, monkeypatch):
    """The real handler over a sandbox home with a flies config, on an ephemeral port."""
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    # STATE_DIR is resolved once at import time, not re-read from the env var above — patch it
    # directly so a halt-flag test here can NEVER touch the real ~/.cherrypick/state/halt-live.flag.
    monkeypatch.setattr(cfgmod, "STATE_DIR", tmp_path / "state")
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "flies.json").write_text(
        json.dumps({"live": {"enabled": False}, "defaults": {"wing_width": 1}}, indent=2),
        encoding="utf-8",
    )
    cfg = {"modules": {"flies": {"enabled": True, "path": "../flies"}}}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), settings_serve._make_handler(cfg, TOKEN, 0))
    port = httpd.server_address[1]
    # the handler validates Host against the port it was built with — rebuild with the real one
    httpd.RequestHandlerClass = settings_serve._make_handler(cfg, TOKEN, port)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()


def _request(url, method="GET", body=None, headers=None):
    # Windows occasionally aborts a fresh loopback connection to a just-started ThreadingHTTPServer
    # (WinError 10053) under rapid create/shutdown cycles across tests — retry once, not a real bug.
    last_exc = None
    for attempt in range(3):
        req = urllib.request.Request(url, method=method, data=body)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()
        except (ConnectionAbortedError, ConnectionResetError, urllib.error.URLError) as exc:
            last_exc = exc
            time.sleep(0.15 * (attempt + 1))
    raise last_exc


def _post(base, path, payload, extra_headers=None, ctype="application/json"):
    headers = {"Content-Type": ctype, "X-Csrf-Token": TOKEN}
    headers.update(extra_headers or {})
    return _request(base + path, "POST", json.dumps(payload).encode(), headers)


def test_get_page_and_state(server):
    status, headers, body = _request(server + "/")
    assert status == 200 and TOKEN.encode() in body
    assert "Access-Control-Allow-Origin" not in headers
    status, _, body = _request(server + "/api/state")
    assert status == 200 and json.loads(body)["ok"] is True


def test_bad_host_forbidden_even_on_get(server):
    status, _, _ = _request(server + "/api/state", headers={"Host": "rebind.attacker.example"})
    assert status == 403
    status, _, _ = _request(
        server + "/api/config/set",
        "POST",
        b"{}",
        {"Host": "rebind.attacker.example", "Content-Type": "application/json", "X-Csrf-Token": TOKEN},
    )
    assert status == 403


def test_post_without_token_forbidden(server):
    status, _, _ = _request(server + "/api/config/set", "POST", b"{}", {"Content-Type": "application/json"})
    assert status == 403
    status, _, _ = _request(
        server + "/api/config/set",
        "POST",
        b"{}",
        {"Content-Type": "application/json", "X-Csrf-Token": "wrong"},
    )
    assert status == 403


def test_post_wrong_content_type_forbidden(server):
    status, _, _ = _post(server, "/api/config/set", {}, ctype="text/plain")
    assert status == 403


def test_post_foreign_origin_forbidden(server):
    status, _, _ = _post(server, "/api/config/set", {}, {"Origin": "https://evil.example"})
    assert status == 403


def test_valid_post_edits_and_guarded_pointer_refused(server):
    status, headers, body = _post(
        server, "/api/config/set", {"target": "flies", "pointer": "/defaults/wing_width", "value": 2}
    )
    assert status == 200 and json.loads(body)["ok"] is True
    assert "Access-Control-Allow-Origin" not in headers
    status, _, body = _post(
        server, "/api/config/set", {"target": "flies", "pointer": "/live/enabled", "value": True}
    )
    out = json.loads(body)
    assert status == 200 and out["ok"] is False and "guarded" in out["error"]


def test_unknown_routes(server):
    status, _, _ = _request(server + "/api/nope")
    assert status == 404
    status, _, body = _post(server, "/api/nope", {})
    assert status == 200 and json.loads(body)["ok"] is False


def test_non_loopback_bind_refused():
    out = settings_serve.serve({"modules": {}}, host="0.0.0.0", open_browser=False)
    assert out["ok"] is False and "loopback" in out["error"]


def test_halt_status_and_toggle_round_trip(server):
    status, _, body = _request(server + "/api/halt")
    out = json.loads(body)
    assert status == 200 and out["ok"] is True and out["present"] is False

    status, _, body = _post(server, "/api/halt/set", {"present": True})
    out = json.loads(body)
    assert status == 200 and out["ok"] is True and out["present"] is True

    status, _, body = _request(server + "/api/halt")
    assert json.loads(body)["present"] is True

    status, _, body = _post(server, "/api/halt/set", {"present": False})
    assert json.loads(body)["present"] is False


def test_halt_toggle_requires_csrf_and_host(server):
    status, _, _ = _request(
        server + "/api/halt/set", "POST", b'{"present": true}', {"Content-Type": "application/json"}
    )
    assert status == 403
    status, _, _ = _request(
        server + "/api/halt/set",
        "POST",
        b'{"present": true}',
        {"Host": "rebind.attacker.example", "Content-Type": "application/json", "X-Csrf-Token": TOKEN},
    )
    assert status == 403
