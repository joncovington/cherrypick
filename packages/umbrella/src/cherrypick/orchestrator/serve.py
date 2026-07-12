"""`dashboard --serve` — a localhost live view of the suite dashboard.

The static dashboard writes an HTML file on each watchdog tick; this serves the *same* page rebuilt
fresh per request so a walk-away user can leave it open and watch health, P&L, and any enabled live
sections update on their own. It reuses `dashboard.build_model` / `_render_html` unchanged — those stay
pure and file-only — and adds one generic route, `/api/section/<id>`, that each section card polls.

Read-only and loopback-only, like the rest of the read side: it reads files (and, for sections,
subprocesses the read-only section module), never the broker, and binds 127.0.0.1 so it is never
exposed off-box.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import dashboard, doctor, embeds, reconcile, sections


def _embed_error(embed_cfg: dict[str, Any], detail: str) -> bytes:
    """A small self-contained page rendered inside an embed iframe when the module dashboard can't be
    delivered (checkout missing, launch/build failed). Keeps the umbrella page intact."""
    from html import escape

    title = escape(str(embed_cfg.get("title", embed_cfg.get("id", "module"))))
    return (
        "<!doctype html><meta charset='utf-8'>"
        '<div style="font:14px system-ui,sans-serif;color:#8a97a3;padding:24px">'
        f"<b>{title}</b> dashboard unavailable<br><span style='font-size:12px'>{escape(detail)}</span>"
        "</div>"
    ).encode()


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

        def _redirect(self, location: str) -> None:
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _serve_embed(self, embed_id: str) -> None:
            """Deliver an embedded module dashboard for its iframe. "server" kind: ensure the module's
            own HTTP dashboard is up (launch in PAPER mode if down) and redirect to its port. "static"
            kind: regenerate (throttled) the module's HTML file and serve it. Best-effort — any failure
            renders an inline message in the iframe, never crashes the server."""
            emb = embeds.by_id(cfg, embed_id)
            if emb is None:
                self._send(404, b"unknown embed", "text/plain")
                return
            try:
                if emb.get("kind") == "server":
                    res = embeds.ensure_server(emb)
                    if res.get("ok"):
                        self._redirect(res["url"])
                    else:
                        self._send(200, _embed_error(emb, res.get("detail", "unavailable")), "text/html")
                    return
                res = embeds.build_static(emb)
                body = embeds.read_static(emb) if res.get("ok") else None
                if body is not None:
                    self._send(200, body, "text/html; charset=utf-8")
                else:
                    self._send(200, _embed_error(emb, res.get("detail", "unavailable")), "text/html")
            except Exception as exc:  # a module hiccup shows inline, never breaks the umbrella server
                self._send(200, _embed_error(emb, str(exc)), "text/html")

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                try:
                    page = dashboard._render_html(dashboard.build_model(cfg), serve=True)
                    self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
                except Exception as exc:  # a render hiccup shows an error page, never crashes the server
                    self._send(500, f"dashboard render error: {exc}".encode(), "text/plain")
                return
            if parsed.path == "/api/system":
                try:
                    # fast=True: the card polls every 30s, so skip the authenticated broker round-trip.
                    checks = doctor.run(cfg, fast=True)
                    payload = {
                        "ok": True,
                        "checks": [
                            {"name": c.name, "status": c.status.upper(), "detail": c.detail} for c in checks
                        ],
                    }
                except Exception as exc:  # a doctor hiccup shows inline, never crashes the server
                    payload = {"ok": False, "error": str(exc)}
                self._send(200, json.dumps(payload).encode("utf-8"), "application/json")
                return
            if parsed.path == "/api/reconcile":
                try:
                    # Broker-touching (get_positions) — so this runs only when the card asks (on load /
                    # button click), never on a background poll. Serve-only, like the doctor card.
                    payload = reconcile.run(cfg)
                except Exception as exc:  # a reconcile hiccup shows inline, never crashes the server
                    payload = {"ok": False, "error": str(exc)}
                self._send(200, json.dumps(payload).encode("utf-8"), "application/json")
                return
            if parsed.path.startswith("/api/section/"):
                sid = parsed.path[len("/api/section/") :]
                sec = sections.by_id(cfg, sid)
                if sec is None:
                    self._send(404, b'{"ok": false, "error": "unknown section"}', "application/json")
                    return
                params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                try:
                    payload = sections.fetch(sec, params)
                except Exception as exc:  # best-effort: a section never breaks the page
                    payload = {"ok": False, "error": str(exc)}
                self._send(200, json.dumps(payload).encode("utf-8"), "application/json")
                return
            if parsed.path.startswith("/embed/"):
                self._serve_embed(parsed.path[len("/embed/") :])
                return
            self._send(404, b"not found", "text/plain")

    return _Handler


def serve(
    cfg: dict[str, Any], host: str | None = None, port: int | None = None, open_browser: bool = True
) -> dict[str, Any]:
    """Run the live suite dashboard until interrupted. Returns a small summary dict when it stops."""
    scfg = cfg.get("dashboard", {}).get("serve", {}) or {}
    host = host or scfg.get("host", "127.0.0.1")
    port = int(port or scfg.get("port", 8787))
    httpd = ThreadingHTTPServer((host, port), _make_handler(cfg))
    url = f"http://{host}:{port}/"
    active = [s["id"] for s in sections.enabled_sections(cfg)]
    active_embeds = [e["id"] for e in embeds.enabled_embeds(cfg)]
    print(
        f"cherrypick dashboard serving at {url}  (Ctrl-C to stop)"
        + (f" · sections: {', '.join(active)}" if active else " · no live sections")
        + (f" · embeds: {', '.join(active_embeds)}" if active_embeds else "")
    )
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return {"ok": True, "served": url}
