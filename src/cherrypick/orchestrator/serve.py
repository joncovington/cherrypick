"""`dashboard --serve` — a localhost live view of the suite dashboard.

The static dashboard writes an HTML file on each watchdog tick; this serves the *same* page rebuilt
fresh per request so a walk-away user can leave it open and watch health, P&L, and (when the GEX module
is enabled) a live GEX section update on their own. It reuses `dashboard.build_model` / `_render_html`
unchanged — those stay pure and file-only — and adds one route, `/api/gex`, that the GEX card polls.

Read-only and loopback-only, like the rest of the read side: it reads files (and, for GEX, subprocesses
the read-only GEX module), never the broker, and binds 127.0.0.1 so it is never exposed off-box.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import dashboard, gex


def _make_handler(cfg: dict[str, Any]):
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the terminal quiet — no per-request spam
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                try:
                    page = dashboard._render_html(dashboard.build_model(cfg), serve=True)
                    self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
                except Exception as exc:  # a render hiccup shows an error page, never crashes the server
                    self._send(500, f"dashboard render error: {exc}".encode(), "text/plain")
                return
            if parsed.path == "/api/gex":
                qs = parse_qs(parsed.query)
                sym = (qs.get("symbol", [gex.default_symbol(cfg)])[0] or gex.default_symbol(cfg))
                try:
                    payload = gex.fetch(cfg, sym)
                except Exception as exc:  # best-effort: the GEX side never breaks the page
                    payload = {"ok": False, "symbol": sym, "error": str(exc)}
                self._send(200, json.dumps(payload).encode("utf-8"), "application/json")
                return
            self._send(404, b"not found", "text/plain")

    return _Handler


def serve(cfg: dict[str, Any], host: str | None = None, port: int | None = None,
          open_browser: bool = True) -> dict[str, Any]:
    """Run the live suite dashboard until interrupted. Returns a small summary dict when it stops."""
    scfg = cfg.get("dashboard", {}).get("serve", {}) or {}
    host = host or scfg.get("host", "127.0.0.1")
    port = int(port or scfg.get("port", 8787))
    httpd = ThreadingHTTPServer((host, port), _make_handler(cfg))
    url = f"http://{host}:{port}/"
    print(f"Cherrypick dashboard serving at {url}  (Ctrl-C to stop)"
          + (f" · GEX: {gex.default_symbol(cfg)}" if gex.is_enabled(cfg) else " · GEX module disabled"))
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return {"ok": True, "served": url}
