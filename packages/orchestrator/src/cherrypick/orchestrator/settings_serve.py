"""`cherrypick settings` — the suite's config editor + secrets manager on 127.0.0.1:8804.

This is deliberately NOT one of the read surfaces: it is the suite's one mutating HTTP server, run
on demand in the foreground (`python run.py settings`), never by the watchdog or a scheduled task.
The write paths live in `configedit` (guarded, validated, backed-up, atomic) and `secretsops`
(keyring only, status-shaped responses); this module only does HTTP.

Because it mutates, loopback binding alone is not enough — a malicious webpage can fetch
http://127.0.0.1:8804 from inside the user's browser, and DNS rebinding can defeat same-origin.
So every request must carry a loopback Host header (else 403), and every POST must additionally
carry the per-session CSRF token baked into the page, an application/json content type (which
forces a cross-origin preflight this server never answers — it sends no CORS headers), and, when an
Origin header is present, the local origin. Secrets in POST bodies are handed straight to
`secretsops` and dropped; no response, log line, or error ever contains one.
"""

from __future__ import annotations

import hmac
import json
import secrets as pysecrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from cherrypick.core import viz

from . import configedit, liveops, secretsops

_MAX_BODY = 256 * 1024
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


def _make_handler(cfg: dict[str, Any], csrf_token: str, port: int):
    valid_hosts = {f"{h}:{port}" for h in _LOOPBACK_HOSTS} | set(_LOOPBACK_HOSTS)
    valid_origins = {f"http://{h}:{port}" for h in _LOOPBACK_HOSTS}

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the terminal quiet — and keep request lines out of any log
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: dict[str, Any], code: int = 200) -> None:
            self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

        def _forbid(self, reason: str) -> None:
            self._json({"ok": False, "error": reason}, code=403)

        def _host_ok(self) -> bool:
            return (self.headers.get("Host") or "") in valid_hosts

        def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler API)
            if not self._host_ok():
                self._forbid("bad Host header")
                return
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/":
                    page = _PAGE.replace("__CSRF__", csrf_token).encode("utf-8")
                    self._send(200, page, "text/html; charset=utf-8")
                elif parsed.path == "/api/state":
                    self._json(
                        {
                            "ok": True,
                            "targets": configedit.targets(cfg),
                            "guarded": {
                                tid: [{"pointer": p, "hint": h} for p, h in ptrs.items()]
                                for tid, ptrs in configedit.GUARDED.items()
                            },
                        }
                    )
                elif parsed.path == "/api/config":
                    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                    loaded = configedit.load(cfg, params.get("target", ""))
                    loaded.pop("doc", None)  # the client parses `text`; don't send the doc twice
                    self._json({"ok": True, **loaded})
                elif parsed.path == "/api/secrets":
                    self._json(secretsops.status(cfg))
                elif parsed.path == "/api/halt":
                    self._json(liveops.halt_status(cfg))
                else:
                    self._send(404, b"not found", "text/plain")
            except Exception as exc:  # any hiccup renders inline, never breaks the server
                self._json({"ok": False, "error": str(exc)})

        def do_POST(self):  # noqa: N802
            if not self._host_ok():
                self._forbid("bad Host header")
                return
            token = self.headers.get("X-Csrf-Token") or ""
            if not hmac.compare_digest(token, csrf_token):
                self._forbid("missing or invalid CSRF token")
                return
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype != "application/json":
                self._forbid("Content-Type must be application/json")
                return
            origin = self.headers.get("Origin")
            if origin and origin not in valid_origins:
                self._forbid("cross-origin POST refused")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 <= length <= _MAX_BODY:
                self._forbid("body too large")
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, OSError):
                self._json({"ok": False, "error": "body is not valid JSON"})
                return
            try:
                self._json(self._dispatch(urlparse(self.path).path, body))
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)})

        def _dispatch(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
            if path == "/api/config/set":
                return configedit.apply_field_edit(
                    cfg,
                    str(body.get("target", "")),
                    str(body.get("pointer", "")),
                    body.get("value"),
                    force=bool(body.get("force")),
                )
            if path == "/api/config/save":
                return configedit.apply_raw_save(
                    cfg, str(body.get("target", "")), str(body.get("text", "")), body.get("mtime")
                )
            if path == "/api/config/organize":
                return configedit.organize(cfg, str(body.get("target", "")), apply=bool(body.get("apply")))
            if path == "/api/secrets/set":
                return secretsops.set_secret(
                    cfg, str(body.get("service", "")), str(body.get("key", "")), body.get("value") or ""
                )
            if path == "/api/secrets/delete":
                return secretsops.delete_secret(cfg, str(body.get("service", "")), str(body.get("key", "")))
            if path == "/api/webhook/set":
                return secretsops.set_webhook(str(body.get("channel", "")), body.get("url") or "")
            if path == "/api/webhook/delete":
                return secretsops.delete_webhook(str(body.get("channel", "")))
            if path == "/api/halt/set":
                return liveops.set_halt(bool(body.get("present")))
            return {"ok": False, "error": "unknown route"}

    return _Handler


def serve(
    cfg: dict[str, Any], host: str | None = None, port: int | None = None, open_browser: bool = True
) -> dict[str, Any]:
    """Run the settings surface until interrupted. Loopback only — a non-loopback host is refused, not
    warned about: this server writes config and secrets, so it must never be laxer than the dashboards."""
    scfg = (cfg.get("settings", {}) or {}).get("serve", {}) or {}
    host = host or scfg.get("host", "127.0.0.1")
    port = int(port or scfg.get("port", 8804))
    if host not in _LOOPBACK_HOSTS:
        return {"ok": False, "error": f"settings binds loopback only (127.0.0.1/localhost), not {host!r}"}
    url = f"http://{host}:{port}/"
    if viz.port_in_use(port, host):
        print(f"settings already serving at {url}")
        return {"ok": True, "served": url, "reused": True}
    csrf_token = pysecrets.token_urlsafe(32)
    httpd = ThreadingHTTPServer((host, port), _make_handler(cfg, csrf_token, port))
    print(f"cherrypick settings serving at {url}  (Ctrl-C to stop) — the suite's one mutating surface")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return {"ok": True, "served": url}


# One baked page, no CDN, no build step — the suite's house style. All data arrives via the JSON
# routes; the only token replaced at serve time is the CSRF token.
_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>cherrypick settings</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font:14px/1.5 system-ui,sans-serif; background:#12161a; color:#d7dee5; }
  header { padding:14px 22px; background:#181e24; border-bottom:1px solid #232c34; }
  header b { font-size:16px; }
  header .warn { color:#e0b453; font-size:12px; margin-left:12px; }
  main { display:flex; gap:0; min-height:calc(100vh - 53px); }
  nav { width:210px; border-right:1px solid #232c34; padding:12px 0; flex-shrink:0; }
  nav a { display:block; padding:7px 20px; color:#9fb0bf; text-decoration:none; cursor:pointer; }
  nav a.on { color:#fff; background:#1d252d; border-left:3px solid #4d9de0; padding-left:17px; }
  nav a small { color:#5c6b78; display:block; font-size:11px; }
  #content { flex:1; padding:18px 26px; max-width:1000px; }
  .card { background:#181e24; border:1px solid #232c34; border-radius:8px; padding:14px 18px;
          margin-bottom:16px; }
  h2 { font-size:15px; margin:0 0 10px; }
  h3.sec { font-size:13px; color:#7fd1a8; margin:20px 0 6px; border-bottom:1px solid #232c34;
           padding-bottom:4px; }
  .note { color:#77848f; font-size:12px; margin:2px 0 8px; white-space:pre-wrap; }
  .row { display:flex; align-items:center; gap:8px; margin:3px 0; }
  .row .k { width:320px; color:#aeb9c4; font-family:ui-monospace,monospace; font-size:12px;
            overflow-wrap:anywhere; flex-shrink:0; }
  .row input[type=text], .row input[type=password] { flex:1; background:#10151a; color:#e6edf3;
            border:1px solid #2a343d; border-radius:4px; padding:4px 8px;
            font-family:ui-monospace,monospace; }
  .row button, .bar button { background:#24303a; color:#cfe3f5; border:1px solid #33414d;
            border-radius:4px; padding:4px 12px; cursor:pointer; }
  .row button:hover, .bar button:hover { background:#2d3c48; }
  .locked { color:#e0b453; font-size:12px; }
  .locked b { font-family:ui-monospace,monospace; }
  .badge { display:inline-block; padding:1px 8px; border-radius:9px; font-size:11px; }
  .set { background:#1d3a2a; color:#7fd1a8; } .unset { background:#3a2626; color:#e08b8b; }
  textarea { width:100%; min-height:420px; background:#10151a; color:#e6edf3; border:1px solid #2a343d;
            border-radius:6px; padding:10px; font:12px ui-monospace,monospace; box-sizing:border-box; }
  .bar { margin:10px 0; display:flex; gap:8px; align-items:center; }
  #msg { font-size:12px; }
  #msg.ok { color:#7fd1a8; } #msg.err { color:#e08b8b; }
  .issue { font-size:12px; } .issue.warn { color:#e0b453; } .issue.error { color:#e08b8b; }
  .tabs { display:flex; gap:6px; margin-bottom:12px; }
  .tabs button.on { background:#4d9de0; color:#0d1218; border-color:#4d9de0; }
  pre.diff { background:#10151a; border:1px solid #2a343d; border-radius:6px; padding:10px;
            font-size:12px; overflow-x:auto; }
  .haltbar { display:flex; justify-content:space-between; align-items:center; gap:12px;
            padding:9px 22px; font-size:13px; border-bottom:1px solid #232c34; }
  .haltbar button { border:1px solid #4a3030; border-radius:4px; padding:5px 14px; cursor:pointer;
            background:#3a2020; color:#f2d9d9; flex-shrink:0; }
  .haltbar button:hover { background:#472626; }
  .halt-idle { background:#181e24; color:#7a8794; }
  .halt-ok { background:#12241a; color:#7fd1a8; }
  .halt-ok button { border-color:#2f4a3a; background:#1d3a2a; color:#d9f2e6; }
  .halt-ok button:hover { background:#25462f; }
  .halt-danger { background:#241414; color:#f2b3b3; }
</style>
</head>
<body>
<header><b>cherrypick settings</b>
  <span class="warn">the suite's one mutating surface — loopback + session-token gated; live-trading
  gates stay read-only here</span></header>
<div id="haltbar"></div>
<main>
  <nav id="nav"></nav>
  <div id="content"></div>
</main>
<script>
"use strict";
const TOKEN = "__CSRF__";
let STATE = null, CURRENT = "secrets", LOADED = null, TAB = "form";

const ESC_MAP = {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"};
const esc = s => String(s).replace(/[&<>"']/g, c => ESC_MAP[c]);
const get = p => fetch(p).then(r => r.json());
const post = (p, body) => fetch(p, {method:"POST", headers:{"Content-Type":"application/json",
  "X-Csrf-Token":TOKEN}, body:JSON.stringify(body)}).then(r => r.json());

function msg(el, text, ok) {
  el.textContent = text; el.className = ok ? "ok" : "err"; el.id = "msg";
}

async function boot() {
  STATE = await get("/api/state");
  renderNav(); show(CURRENT); renderHalt();
  setInterval(() => { if (CURRENT === "secrets") show("secrets", true); renderHalt(); }, 15000);
}

async function renderHalt() {
  const h = await get("/api/halt");
  const bar = document.getElementById("haltbar");
  if (!h.ok) { bar.innerHTML = ""; return; }
  const halted = h.present, live = h.any_live_enabled;
  const cls = halted ? "halt-ok" : (live ? "halt-danger" : "halt-idle");
  const live_modules = h.modules.filter(m => m.live_enabled).map(m => m.module).join(", ");
  const label = halted
    ? `Halt flag is SET — new live entries are blocked (${esc(h.path)}).`
    : (live ? `Live trading enabled for: ${esc(live_modules)} — no halt flag set.`
            : "No module has live trading enabled.");
  bar.className = "haltbar " + cls;
  bar.innerHTML = `<span>${label}</span>
    <button id="haltbtn">${halted ? "Clear halt" : "Halt live trading now"}</button>`;
  document.getElementById("haltbtn").onclick = async () => {
    const prompt = halted
      ? "Clear the suite halt flag? Live loops can open new entries again on their next tick."
      : "Set the suite halt flag? This blocks NEW live entries on every module — open positions " +
        "still follow their normal hold-to-settlement rules. It does not flip enable_live_trading " +
        "and does not touch any module's code or config.";
    if (!confirm(prompt)) return;
    const res = await post("/api/halt/set", {present: !halted});
    if (!res.ok) alert(res.error || "failed");
    renderHalt();
  };
}

function renderNav() {
  const nav = document.getElementById("nav");
  let h = "";
  for (const t of STATE.targets)
    h += `<a data-id="${esc(t.id)}">${esc(t.title)}<small>${esc(t.portable)}` +
         `${t.exists ? "" : " · not present"}</small></a>`;
  h += '<a data-id="secrets">secrets & accounts</a>';
  nav.innerHTML = h;
  nav.querySelectorAll("a").forEach(a => a.onclick = () => show(a.dataset.id));
  markNav();
}
function markNav() {
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("on", a.dataset.id === CURRENT));
}

async function show(id, quiet) {
  CURRENT = id; markNav();
  if (id === "secrets") return renderSecrets(quiet);
  LOADED = await get("/api/config?target=" + encodeURIComponent(id));
  renderTarget();
}

/* ---------------- config targets ---------------- */

function renderTarget() {
  const c = document.getElementById("content");
  if (!LOADED.exists) {
    c.innerHTML = `<div class="card"><h2>${esc(LOADED.id)}</h2>
      <p class="note">No config file at ${esc(LOADED.portable)} yet — it is created by that module's own
      setup, never scaffolded from here.</p></div>`;
    return;
  }
  const issues = (LOADED.issues || []).map(([lvl, m]) =>
    `<div class="issue ${lvl}">${esc(lvl)}: ${esc(m)}</div>`).join("");
  c.innerHTML = `
    <div class="tabs">
      <button id="tab-form" class="${TAB==='form'?'on':''}">Form</button>
      <button id="tab-raw" class="${TAB==='raw'?'on':''}">Raw</button>
      <button id="organize">Organize into sections…</button>
      <span id="msg"></span>
    </div>
    ${issues}
    <div id="body"></div>`;
  document.getElementById("tab-form").onclick = () => { TAB = "form"; renderTarget(); };
  document.getElementById("tab-raw").onclick = () => { TAB = "raw"; renderTarget(); };
  document.getElementById("organize").onclick = organize;
  if (TAB === "form") renderForm(); else renderRaw();
}

function guardFor(pointer) {
  return ((STATE.guarded[LOADED.id] || []).find(g => g.pointer === pointer)) || null;
}

function renderForm() {
  const doc = JSON.parse(LOADED.text);
  const out = [];
  walk(doc, "", out, 0);
  document.getElementById("body").innerHTML = `<div class="card">${out.join("")}</div>`;
  document.querySelectorAll("#body button[data-ptr]").forEach(b => b.onclick = () => saveField(b));
}

function walk(node, base, out, depth) {
  for (const key of Object.keys(node)) {
    const ptr = base + "/" + key.replace(/~/g, "~0").replace(/\//g, "~1");
    const val = node[key];
    if (key.endsWith("_header") && typeof val === "string") {
      out.push(`<h3 class="sec">${esc(String(val).replace(/=+/g, "").trim() || key)}</h3>`);
    } else if (key.startsWith("_") && typeof val === "string") {
      out.push(`<div class="note">${esc(val)}</div>`);
    } else if (val !== null && typeof val === "object" && !Array.isArray(val) && depth < 2) {
      out.push(`<div class="row"><span class="k"><b>${esc(key)}</b></span></div>`);
      out.push(`<div style="margin-left:18px">`);
      walk(val, ptr, out, depth + 1);
      out.push(`</div>`);
    } else {
      const g = guardFor(ptr);
      if (g) {
        out.push(`<div class="row"><span class="k">${esc(key)}</span>
          <span class="locked">&#128274; <b>${esc(JSON.stringify(val))}</b> — ${esc(g.hint)}</span></div>`);
      } else {
        out.push(`<div class="row"><span class="k">${esc(key)}</span>
          <input type="text" value="${esc(JSON.stringify(val))}" data-orig="${esc(JSON.stringify(val))}">
          <button data-ptr="${esc(ptr)}">Save</button></div>`);
      }
    }
  }
}

async function saveField(btn) {
  const input = btn.previousElementSibling;
  const m = document.getElementById("msg");
  let value;
  try { value = JSON.parse(input.value); }
  catch { value = input.value; }  // bare text means a string
  let res = await post("/api/config/set", {target: LOADED.id, pointer: btn.dataset.ptr, value});
  if (!res.ok && res.error && res.error.includes("force")) {
    if (confirm(res.error + "\n\nApply anyway?"))
      res = await post("/api/config/set", {target: LOADED.id, pointer: btn.dataset.ptr, value, force: true});
  }
  if (res.ok) {
    msg(m, "saved" + (res.backup ? " · backup " + res.backup : ""), true);
    LOADED = await get("/api/config?target=" + encodeURIComponent(LOADED.id));
    renderTarget();
  } else msg(m, res.error || "failed", false);
}

function renderRaw() {
  document.getElementById("body").innerHTML = `<div class="card">
    <textarea id="rawtext">${esc(LOADED.text)}</textarea>
    <div class="bar"><button id="rawsave">Validate &amp; save</button></div></div>`;
  document.getElementById("rawsave").onclick = async () => {
    const m = document.getElementById("msg");
    const res = await post("/api/config/save",
      {target: LOADED.id, text: document.getElementById("rawtext").value, mtime: LOADED.mtime});
    if (res.ok) {
      msg(m, res.unchanged ? "no changes" : "saved · backup " + res.backup, true);
      LOADED = await get("/api/config?target=" + encodeURIComponent(LOADED.id));
      renderTarget();
    } else msg(m, res.error || "failed", false);
  };
}

async function organize() {
  const m = document.getElementById("msg");
  const dry = await post("/api/config/organize", {target: LOADED.id});
  if (!dry.ok) return msg(m, dry.error, false);
  if (!dry.changed) return msg(m, "already organized", true);
  if (!confirm("Reorder this file into its example's sections? Values and notes are unchanged; " +
               "a timestamped backup is written first.")) return;
  const res = await post("/api/config/organize", {target: LOADED.id, apply: true});
  if (res.ok) {
    msg(m, "organized · backup " + (res.backup || ""), true);
    LOADED = await get("/api/config?target=" + encodeURIComponent(LOADED.id));
    renderTarget();
  } else msg(m, res.error, false);
}

/* ---------------- secrets ---------------- */

async function renderSecrets(quiet) {
  const data = await get("/api/secrets");
  if (CURRENT !== "secrets") return;
  const c = document.getElementById("content");
  if (!data.ok) {
    c.innerHTML = `<div class="card issue error">${esc(data.error || "unavailable")}</div>`;
    return;
  }
  let h = `<div class="card"><h2>Broker credentials</h2>
    <p class="note">Values are write-only: this page shows set/not-set and masked accounts, never a
    secret. A value you enter is written straight to the OS keyring and dropped.</p>`;
  for (const [svc, info] of Object.entries(data.services)) {
    const modules = info.modules.length ? " (" + info.modules.join(", ") + ")" : "";
    h += `<h3 class="sec">${esc(svc)} — ${esc(info.label)}${modules}</h3>`;
    for (const [key, isSet] of Object.entries(info.status)) {
      const acct = key === "account_number" && info.account
        ? ` <span class="note" style="display:inline">${esc(info.account)}</span>` : "";
      h += `<div class="row"><span class="k">${esc(key)}
          <span class="badge ${isSet ? "set" : "unset"}">${isSet ? "set" : "not set"}</span>${acct}</span>
        <input type="password" placeholder="new value" autocomplete="off">
        <button data-svc="${esc(svc)}" data-key="${esc(key)}" data-op="set">Set</button>
        <button data-svc="${esc(svc)}" data-key="${esc(key)}" data-op="delete">Delete</button></div>`;
    }
  }
  h += `</div><div class="card"><h2>Notification webhooks</h2>`;
  for (const [ch, st] of Object.entries(data.webhooks)) {
    h += `<div class="row"><span class="k">${esc(ch)}
        <span class="badge ${st === "set" ? "set" : "unset"}">${esc(st)}</span></span>
      <input type="password" placeholder="https://…" autocomplete="off">
      <button data-ch="${esc(ch)}" data-op="whset">Set</button>
      <button data-ch="${esc(ch)}" data-op="whdel">Delete</button></div>`;
  }
  h += `<span id="msg"></span></div>`;
  c.innerHTML = h;
  c.querySelectorAll("button[data-op]").forEach(b => b.onclick = () => secretAction(b));
}

async function secretAction(btn) {
  const input = btn.parentElement.querySelector("input");
  const op = btn.dataset.op;
  let res;
  if (op === "set") {
    if (!input.value) return;
    res = await post("/api/secrets/set",
      {service: btn.dataset.svc, key: btn.dataset.key, value: input.value});
    input.value = "";
  } else if (op === "delete") {
    if (!confirm(`Delete ${btn.dataset.key} from ${btn.dataset.svc}?`)) return;
    res = await post("/api/secrets/delete", {service: btn.dataset.svc, key: btn.dataset.key});
  } else if (op === "whset") {
    if (!input.value) return;
    res = await post("/api/webhook/set", {channel: btn.dataset.ch, url: input.value});
    input.value = "";
  } else {
    if (!confirm(`Delete the ${btn.dataset.ch} webhook?`)) return;
    res = await post("/api/webhook/delete", {channel: btn.dataset.ch});
  }
  await renderSecrets();
  const m = document.getElementById("msg");
  if (m) msg(m, res.ok ? "done" : (res.error || "failed"), res.ok);
}

boot();
</script>
</body>
</html>
"""
