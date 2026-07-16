"""serve: the standalone GEX page renders all three tabs, and the HTTP handler wires / and /api/gex.

Parity guard — the page must keep the GEX / IV-Skew / Volume tabs, their charts, the spot-trail
plugin, and a populated symbol selector, so it stays a full replacement for MEIC's old GEX view.
"""

import http.client
import threading
from http.server import ThreadingHTTPServer

import serve


def test_render_page_has_all_three_tabs_and_charts():
    page = serve._render_page("SPX", 15, ["SPX", "XSP"]).decode()
    for marker in (
        'data-gex-tab="gex"', 'data-gex-tab="ivskew"', 'data-gex-tab="volume"',
        'id="gex-main-chart"', 'id="gex-iv-chart"', 'id="gex-oi-chart"', 'id="gex-vol-chart"',
        "_spotHistoryPlugin", "renderGexMainChart", "renderIvChart", "renderVolChart",
    ):
        assert marker in page, f"missing {marker}"


def test_render_page_populates_and_selects_symbol():
    page = serve._render_page("XSP", 15, ["SPX", "XSP"]).decode()
    assert '<option value="SPX">SPX</option>' in page
    assert '<option value="XSP" selected>XSP</option>' in page  # default symbol pre-selected


def test_handler_serves_page_and_api(tmp_path):
    cfg = {
        "serve": {"refresh_seconds": 15},
        "symbols": ["SPX"],
        "stream_cache_db": str(tmp_path / "nope.db"),   # missing -> build_gex returns ok:false gracefully
        "history_db_path": str(tmp_path / "hist.db"),
    }
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve.make_handler(cfg, "SPX"))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/")
        r = conn.getresponse()
        body = r.read().decode()
        assert r.status == 200 and "Cherrypick GEX" in body and 'data-gex-tab="volume"' in body

        conn.request("GET", "/api/gex?symbol=SPX")
        r = conn.getresponse()
        assert r.status == 200
        import json
        payload = json.loads(r.read())
        assert payload["ok"] is False and "streamer" in payload["error"]  # missing cache, handled cleanly
    finally:
        httpd.shutdown()
        httpd.server_close()
