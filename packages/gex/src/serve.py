"""The module's own ``dashboard --serve`` — a localhost live GEX view.

A stdlib ``ThreadingHTTPServer`` (no framework, no auth, loopback-only) serving one self-contained page
that polls ``/api/gex`` and draws a SpotGamma/MenthorQ-style panel: net GEX by strike with **open
interest vs traded volume** side by side, the zero-gamma (gamma-flip) level, call/put walls, and a live
spot marker + intraday spot trail. Read-only: every refresh just re-reads MEIC's stream cache via
``service.build_gex``.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import service as _service

_PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cherrypick GEX — __SYMBOL__</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root { color-scheme: light dark; --bg:#0f1116; --card:#181b22; --fg:#e7e9ee; --mut:#98a2b3;
        --call:#22a06b; --put:#d1495b; --vol:#e0a800; --line:#4a6cf7; --grid:#262b36; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.4 system-ui,Segoe UI,Roboto,sans-serif; }
header { display:flex; align-items:baseline; gap:16px; padding:14px 20px; border-bottom:1px solid var(--grid); flex-wrap:wrap; }
h1 { font-size:18px; margin:0; font-weight:650; }
.sub { color:var(--mut); font-size:13px; }
main { padding:16px 20px; max-width:1100px; margin:0 auto; }
.metrics { display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:10px; margin-bottom:16px; }
.metric { background:var(--card); border:1px solid var(--grid); border-radius:10px; padding:10px 12px; }
.metric .k { color:var(--mut); font-size:11px; text-transform:uppercase; letter-spacing:.04em; }
.metric .v { font-size:18px; font-weight:650; margin-top:2px; }
.controls { display:flex; gap:14px; align-items:center; margin-bottom:10px; color:var(--mut); font-size:13px; }
.chartwrap { background:var(--card); border:1px solid var(--grid); border-radius:12px; padding:12px; }
canvas { width:100% !important; height:460px !important; }
.pos { color:var(--call); } .neg { color:var(--put); }
.err { background:#3a1d22; border:1px solid var(--put); color:#ffd7dc; padding:10px 12px; border-radius:8px; }
.foot { color:var(--mut); font-size:12px; margin-top:12px; }
label { cursor:pointer; user-select:none; }
</style></head>
<body>
<header>
  <h1>Cherrypick GEX</h1>
  <span class="sub" id="sub">loading…</span>
  <span class="sub" style="margin-left:auto" id="refresh"></span>
</header>
<main>
  <div id="err" class="err" style="display:none"></div>
  <div class="metrics" id="metrics"></div>
  <div class="controls">
    <label><input type="radio" name="view" value="oivol" checked> Net GEX: OI vs Volume</label>
    <label><input type="radio" name="view" value="oi"> Net GEX (OI)</label>
    <label><input type="radio" name="view" value="abs"> Absolute GEX</label>
  </div>
  <div class="chartwrap"><canvas id="chart"></canvas></div>
  <div class="foot">Positioning = open interest · Flow = traded volume · GEX in $ per 1% move.
    Source: MEIC stream cache (read-only). A simple self-hosted take on gexbot / SpotGamma / MenthorQ.</div>
</main>
<script>
const REFRESH = __REFRESH__ * 1000;
let SYMBOL = "__SYMBOL__";
let chart, lastData = null, view = "oivol";

document.querySelectorAll('input[name=view]').forEach(r =>
  r.addEventListener('change', e => { view = e.target.value; if (lastData) render(lastData); }));

function fmtBn(v){ if(v==null) return '–'; const a=Math.abs(v);
  if(a>=1e9) return (v/1e9).toFixed(2)+'B'; if(a>=1e6) return (v/1e6).toFixed(1)+'M';
  if(a>=1e3) return (v/1e3).toFixed(0)+'K'; return v.toFixed(0); }

// Draw vertical reference lines (spot / zero-gamma / walls) over the bars.
const levelLines = {
  id: 'levelLines',
  afterDatasetsDraw(c){
    if(!lastData) return; const {ctx, chartArea:a, scales:{x}} = c;
    const marks = [
      ['spot', lastData.underlying_price, '#e7e9ee'],
      ['flip', lastData.totals.zero_gamma, '#4a6cf7'],
      ['call wall', lastData.totals.call_wall, '#22a06b'],
      ['put wall', lastData.totals.put_wall, '#d1495b'],
    ];
    ctx.save(); ctx.font='11px system-ui'; ctx.textAlign='center';
    for(const [name,val,col] of marks){
      if(val==null) continue; const px = x.getPixelForValue(val); if(isNaN(px)) continue;
      ctx.strokeStyle=col; ctx.lineWidth=1.5; ctx.setLineDash(name==='spot'?[]:[5,4]);
      ctx.beginPath(); ctx.moveTo(px,a.top); ctx.lineTo(px,a.bottom); ctx.stroke();
      ctx.fillStyle=col; ctx.fillText(name, px, a.top-2);
    }
    ctx.restore();
  }
};

function render(d){
  lastData = d;
  const strikes = d.series.map(s => s.strike);
  let datasets;
  if(view==='oivol'){
    datasets = [
      {label:'Net GEX (OI)', data:d.series.map(s=>s.net_gex),
       backgroundColor:d.series.map(s=>s.net_gex>=0?'#22a06b':'#d1495b')},
      {label:'Net GEX (Volume)', data:d.series.map(s=>s.net_gex_vol),
       backgroundColor:'rgba(224,168,0,.75)'},
    ];
  } else if(view==='abs'){
    datasets = [{label:'Absolute GEX', data:d.series.map(s=>s.abs_gex), backgroundColor:'#4a6cf7'}];
  } else {
    datasets = [{label:'Net GEX (OI)', data:d.series.map(s=>s.net_gex),
       backgroundColor:d.series.map(s=>s.net_gex>=0?'#22a06b':'#d1495b')}];
  }
  const cfg = {
    type:'bar',
    data:{ labels:strikes, datasets },
    options:{
      responsive:true, maintainAspectRatio:false, animation:false,
      scales:{ x:{ type:'linear', title:{display:true,text:'Strike'}, grid:{color:'#262b36'} },
               y:{ title:{display:true,text:'Net GEX ($/1%)'}, grid:{color:'#262b36'},
                   ticks:{ callback:v=>fmtBn(v) } } },
      plugins:{ legend:{ labels:{color:'#98a2b3'} },
                tooltip:{ callbacks:{ label:c=>c.dataset.label+': '+fmtBn(c.parsed.y) } } }
    },
    plugins:[levelLines]
  };
  if(chart) chart.destroy();
  chart = new Chart(document.getElementById('chart'), cfg);
}

function renderMetrics(d){
  const t = d.totals;
  const cells = [
    ['Spot', d.underlying_price!=null? d.underlying_price.toFixed(2):'–', ''],
    ['Net GEX', fmtBn(t.net_gex), t.net_gex>=0?'pos':'neg'],
    ['Total Call GEX', fmtBn(t.total_call_gex), 'pos'],
    ['Total Put GEX', fmtBn(t.total_put_gex), 'neg'],
    ['Gamma Flip', t.zero_gamma!=null? t.zero_gamma.toFixed(2):'–', ''],
    ['Call Wall', t.call_wall!=null? t.call_wall:'–', 'pos'],
    ['Put Wall', t.put_wall!=null? t.put_wall:'–', 'neg'],
    ['Max GEX Strike', t.max_gex_strike!=null? t.max_gex_strike:'–', ''],
  ];
  document.getElementById('metrics').innerHTML = cells.map(([k,v,c]) =>
    `<div class="metric"><div class="k">${k}</div><div class="v ${c}">${v}</div></div>`).join('');
}

async function tick(){
  try{
    const r = await fetch('/api/gex?symbol='+encodeURIComponent(SYMBOL));
    const d = await r.json();
    const err = document.getElementById('err');
    if(!d.ok){ err.style.display='block'; err.textContent = d.error || 'no data'; return; }
    err.style.display='none';
    document.getElementById('sub').textContent =
      `${d.symbol} · exp ${d.expiration||'?'} · ${d.series.length} strikes · ${d.source}`;
    renderMetrics(d); render(d);
  }catch(e){ /* transient; keep last chart */ }
}
let secs = __REFRESH__;
setInterval(()=>{ document.getElementById('refresh').textContent = 'refresh in '+secs+'s';
  if(--secs<0){ secs=__REFRESH__; tick(); } }, 1000);
tick();
</script>
</body></html>"""


def _render_page(symbol: str, refresh: int) -> bytes:
    html = (_PAGE.replace("__SYMBOL__", symbol)
                 .replace("__REFRESH__", str(refresh)))
    return html.encode("utf-8")


def make_handler(cfg: dict, default_sym: str):
    refresh = int(cfg["serve"].get("refresh_seconds", 15))

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet — no request-log spam
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
                self._send(200, _render_page(default_sym, refresh), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/gex":
                qs = parse_qs(parsed.query)
                sym = (qs.get("symbol", [default_sym])[0] or default_sym)
                try:
                    payload = _service.build_gex(cfg, sym)
                except Exception as exc:  # a data hiccup must not 500 the viewer
                    payload = {"ok": False, "symbol": sym, "error": str(exc)}
                self._send(200, json.dumps(payload).encode("utf-8"), "application/json")
                return
            self._send(404, b"not found", "text/plain")

    return _Handler


def serve(cfg: dict, symbol: str | None = None, host: str | None = None,
          port: int | None = None, open_browser: bool = True) -> None:
    """Run the live GEX dashboard until interrupted (localhost-only)."""
    from config import default_symbol

    sym = (symbol or default_symbol(cfg)).strip().upper()
    host = host or cfg["serve"].get("host", "127.0.0.1")
    port = int(port or cfg["serve"].get("port", 5055))
    httpd = ThreadingHTTPServer((host, port), make_handler(cfg, sym))
    url = f"http://{host}:{port}/"
    print(f"cherrypick-gex dashboard serving {sym} at {url}  (Ctrl-C to stop)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
