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
    # Per-request threads must not outlive the test that made them: a handler still writing to a
    # socket after teardown is one of the ways a later test meets a connection nobody is serving.
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    # the handler validates Host against the port it was built with — rebuild with the real one
    httpd.RequestHandlerClass = settings_serve._make_handler(cfg, TOKEN, port)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    # Join before closing the listening socket. Without it `server_close()` can pull the socket out
    # from under a serve_forever loop that has not finished unwinding, and the ephemeral port can be
    # handed to the NEXT test's server while the old one is still tearing down — which presents as a
    # connection error in a test that has nothing to do with the one that leaked it.
    t.join(timeout=5)
    httpd.server_close()


def _request(url, method="GET", body=None, headers=None):
    """One request, retried past the transport flakiness of loopback servers on Windows.

    A fresh connection to a just-started ThreadingHTTPServer is occasionally aborted or refused under
    the rapid create/shutdown cycling these tests do (WinError 10053/10061). That is the test harness
    failing, not the gate under test — and a transport flake that reads as "the CSRF check let a POST
    through" is worse than a slow test, because it trains you to re-run reds instead of reading them.

    Catch `OSError`, not a hand-listed pair: `ConnectionRefusedError` is neither of the two named
    before, and `URLError` (which urllib wraps most of these in) is itself an OSError, so the broad
    clause is both simpler and strictly wider. `HTTPError` is caught first and returned — an HTTP
    status IS the answer here, never something to retry.
    """
    last_exc = None
    for attempt in range(5):
        req = urllib.request.Request(url, method=method, data=body)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()
        except OSError as exc:
            last_exc = exc
            time.sleep(0.1 * 2**attempt)  # 0.1, 0.2, 0.4, 0.8 — ~1.5s total before giving up
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
