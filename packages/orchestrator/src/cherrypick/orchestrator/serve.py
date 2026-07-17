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

import html
import json
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import config as cfgmod
from . import dashboard, doctor, embeds, reconcile, sections

_SESSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# The handful of literal HTML entities the reports embed (e.g. the digest's "&minus;"/"&middot;").
# html.escape turns them into "&amp;minus;" etc.; restore just these so they render as intended.
_KEEP_ENTITIES = ("minus", "middot", "divide", "nbsp", "times")


def _md_inline(s: str) -> str:
    """Escape a line, then apply inline markdown (bold / italic / code). Markdown markers survive
    html.escape (they aren't HTML-special), so escaping first keeps the render injection-safe."""
    s = html.escape(s)
    for ent in _KEEP_ENTITIES:
        s = s.replace(f"&amp;{ent};", f"&{ent};")
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    # Italic: only when the underscores bound a run (so intra-word names like per_side_stop don't match).
    s = re.sub(r"(?<![A-Za-z0-9])_(.+?)_(?![A-Za-z0-9])", r"<em>\1</em>", s)
    return s


def _md_list(items: list[tuple[int, str]]) -> str:
    """Render `- ` bullets into a <ul>, nesting deeper-indented items under the preceding one (the
    reports use a single 2-space nesting level)."""
    base = min(ind for ind, _ in items)
    out = ["<ul>"]
    i, n = 0, len(items)
    while i < n:
        ind, text = items[i]
        j = i + 1
        children = []
        while j < n and items[j][0] > base:
            children.append(items[j][1])
            j += 1
        if children:
            sub = "<ul>" + "".join(f"<li>{c}</li>" for c in children) + "</ul>"
            out.append(f"<li>{text}{sub}</li>")
        else:
            out.append(f"<li>{text}</li>")
        i = j
    out.append("</ul>")
    return "".join(out)


def _md_to_html(md_text: str) -> str:
    """A tiny, dependency-free markdown → HTML converter for exactly the subset the EOD reports use:
    ATX headings, pipe tables, `- ` bullets (one nesting level), bold/italic/inline-code, paragraphs.
    Not a general markdown engine — just enough to render our own generated files nicely."""
    lines = md_text.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        # Pipe table: a "| … |" row immediately followed by a "|---|---|" separator.
        if stripped.startswith("|") and i + 1 < n and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            header = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2
            body = []
            while i < n and lines[i].strip().startswith("|"):
                body.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            thead = "".join(f"<th>{_md_inline(c)}</th>" for c in header)
            rows = "".join(
                "<tr>" + "".join(f"<td>{_md_inline(c)}</td>" for c in r) + "</tr>" for r in body
            )
            out.append(f'<div class="tbl"><table><thead><tr>{thead}</tr></thead>'
                       f"<tbody>{rows}</tbody></table></div>")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_md_inline(m.group(2))}</h{lvl}>")
            i += 1
            continue
        if re.match(r"^\s*-\s+", line):
            items: list[tuple[int, str]] = []
            while i < n and re.match(r"^\s*-\s+", lines[i]):
                indent = len(lines[i]) - len(lines[i].lstrip())
                items.append((indent, _md_inline(re.sub(r"^\s*-\s+", "", lines[i]))))
                i += 1
            out.append(_md_list(items))
            continue
        out.append(f"<p>{_md_inline(stripped)}</p>")
        i += 1
    return "\n".join(out)


def _md_page(title: str, md_text: str) -> bytes:
    """Render an EOD markdown report as a styled, self-contained dark page matching the dashboard —
    headings, pipe tables, bullets, and emphasis rendered as real HTML (no external dependency). The
    report's own leading `# …` H1 becomes the page heading, so no separate title bar is needed."""
    body = _md_to_html(md_text)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title>"
        "<style>"
        ":root{--bg:#0a0e12;--fg:#e6edf3;--muted:#8b98a5;--grid:#23303c;--card:#0f151b;--accent:#58a6ff}"
        "*{box-sizing:border-box}"
        "body{background:var(--bg);color:var(--fg);margin:0;"
        "font:14px/1.65 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}"
        ".wrap{max-width:960px;margin:0 auto;padding:28px 24px 64px}"
        "h1{font-size:22px;margin:0 0 4px;padding-bottom:12px;border-bottom:1px solid var(--grid)}"
        "h2{font-size:16px;margin:26px 0 8px;color:var(--fg)}"
        "h3{font-size:14px;margin:18px 0 6px;color:var(--muted);text-transform:uppercase}"
        "p{margin:8px 0}"
        "em{color:var(--muted);font-style:italic}"
        "strong{color:var(--fg)}"
        "code{font:12.5px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;"
        "background:#161d25;border:1px solid var(--grid);border-radius:4px;padding:1px 5px}"
        "ul{margin:8px 0;padding-left:22px}li{margin:3px 0}"
        ".tbl{overflow-x:auto;margin:10px 0}"
        "table{border-collapse:collapse;width:100%;font-size:13px}"
        "th,td{padding:6px 12px;text-align:left;border-bottom:1px solid var(--grid);white-space:nowrap}"
        "th{color:var(--muted);font-weight:600;border-bottom:2px solid var(--grid)}"
        "tbody tr:hover{background:var(--card)}"
        "td:first-child,th:first-child{padding-left:2px}"
        "</style></head>"
        f"<body><div class='wrap'>{body}</div></body></html>"
    ).encode()


def _embed_error(embed_cfg: dict[str, Any], detail: str) -> bytes:
    """A small self-contained page rendered inside an embed iframe when the module dashboard can't be
    delivered (checkout missing, launch/build failed). Keeps the orchestrator page intact."""
    from html import escape

    title = escape(str(embed_cfg.get("title", embed_cfg.get("id", "module"))))
    return (
        "<!doctype html><meta charset='utf-8'>"
        '<div style="font:14px system-ui,sans-serif;color:#8a97a3;padding:24px">'
        f"<b>{title}</b> dashboard unavailable<br><span style='font-size:12px'>{escape(detail)}</span>"
        "</div>"
    ).encode()


def _embed_building(embed_cfg: dict[str, Any]) -> bytes:
    """A lightweight placeholder shown while a static embed's first build runs in the background. It
    auto-refreshes so the iframe swaps to the real dashboard as soon as the file lands — the request
    never blocks on the generator."""
    from html import escape

    title = escape(str(embed_cfg.get("title", embed_cfg.get("id", "module"))))
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<meta http-equiv='refresh' content='2'>"
        '<div style="font:14px system-ui,sans-serif;color:#8a97a3;padding:24px">'
        f"<b>{title}</b> dashboard building…<br>"
        "<span style='font-size:12px'>generating charts; this view refreshes automatically.</span>"
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
                elif res.get("building"):
                    # First build is running in the background — show an auto-refreshing placeholder
                    # rather than blocking the iframe on the generator.
                    self._send(200, _embed_building(emb), "text/html; charset=utf-8")
                else:
                    self._send(200, _embed_error(emb, res.get("detail", "unavailable")), "text/html")
            except Exception as exc:  # a module hiccup shows inline, never breaks the orchestrator server
                self._send(200, _embed_error(emb, str(exc)), "text/html")

        def _serve_eod_report(self, params: dict[str, list[str]]) -> None:
            """Serve an EOD markdown report — a module's terse `paper-eod-<day>.md` (kind=report,
            default) or conversational `eod-analysis-<day>.md` (kind=analysis), or the suite digest —
            as a readable page opened in a new tab. Path-traversal-safe: session is regex-validated,
            `kind` selects between two fixed filenames, and the path is derived from config resolvers +
            the validated day, never from client input."""
            session = (params.get("session") or [""])[0]
            module = (params.get("module") or [None])[0]
            kind = (params.get("kind") or ["report"])[0]
            is_suite = bool(params.get("suite"))
            is_insight = bool(params.get("insight"))
            if not _SESSION_RE.match(session):
                self._send(400, b"bad session", "text/plain")
                return
            if is_insight:
                path = cfgmod.log_file(f"eod-insight-{session}.md")
                title = f"suite EOD insight — {session}"
            elif is_suite:
                path = cfgmod.log_file(f"eod-digest-{session}.md")
                title = f"suite EOD digest — {session}"
            else:
                if module not in cfgmod.enabled_modules(cfg):
                    self._send(404, b"unknown module", "text/plain")
                    return
                if kind == "analysis":
                    path = cfgmod.module_logs_dir(module) / f"eod-analysis-{session}.md"
                    title = f"{module} EOD analysis — {session}"
                else:
                    path = cfgmod.module_logs_dir(module) / f"paper-eod-{session}.md"
                    title = f"{module} EOD report — {session}"
            if not path.exists():
                self._send(404, b"report not found", "text/plain")
                return
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                self._send(500, f"read error: {exc}".encode(), "text/plain")
                return
            self._send(200, _md_page(title, text), "text/html; charset=utf-8")

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/eod-report":
                self._serve_eod_report(parse_qs(parsed.query))
                return
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
    # Pre-warm static embeds in the background so their (matplotlib) HTML already exists by the time the
    # user opens the page — the first iframe load then serves instantly instead of triggering a build.
    embeds.prewarm(cfg)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return {"ok": True, "served": url}
