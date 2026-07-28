"""cherrypick.core.viz — a declarative dashboard-section contract + one generic renderer.

A module contributes a live card to the umbrella dashboard by emitting a small JSON payload (below); the
umbrella renders any section with no section-specific code, so a new module gets a card "for free" by
speaking this schema. This generalizes what was hand-wired for the GEX card. No broker, no network — the
umbrella subprocesses the module for the payload; this module only holds the schema, styles, and client
renderer.

Section payload (a module emits this per refresh):

    {
      "ok": true,
      "title": "GEX — SPX",            # optional; falls back to the section's configured title
      "subtitle": "exp 2026-07-10 ...",# optional line under the title
      "metrics": [                     # KPI tiles; value is a pre-formatted string (module owns units)
        {"label": "Net GEX", "value": "73.7B", "tone": "pos"},   # tone: pos|neg|accent|"" (default)
        ...
      ],
      "bars": {                        # optional compact labelled bar chart (signed, zero-centred)
        "labels": [7500, 7510, ...],   # numeric x positions (one per row)
        "focus": 7575.39,              # optional: highlight the row nearest this value (e.g. spot)
        "series": [                    # 1-2 series drawn as overlaid bars per row
          {"name": "Net GEX (OI)", "values": [...], "tone_by_sign": true},
          {"name": "Net GEX (Vol)", "values": [...], "tone": "vol"}
        ]
      },
      "note": "positioning = OI ..."   # optional footer
    }

    # or, when the module has nothing to show yet:
    {"ok": false, "error": "streamer not running"}

Optionally a section may instead (or additionally) carry a TIME SERIES — the suite's
date-axis chart type (equity curves, VIX overlays, completion-rate trends), rendered on a
plain canvas (offline-safe: no CDN, no external assets — the flies dashboard's rule,
promoted here):

    "timeseries": {
      "labels": ["2026-07-21", ...],   # x categories (ISO dates; sparse ticks drawn)
      "series": [                      # 1-4 lines on the left axis
        {"name": "suite", "values": [...], "tone": "accent"},
        ...
      ],
      "overlay": {"name": "VIX", "values": [...]},  # optional dashed right-axis line
      "markers": [{"label": "epoch", "at": "2026-07-21"}]  # optional vertical annotations
    }

A null in `values` BREAKS the line rather than interpolating across it — a gap in the data
must look like a gap (the flies timeline honesty rule).

`tone` values map to CSS classes (pos/neg/accent/vol); anything else renders neutral.

Cards come in two wirings: `card_skeleton_html` (polls `data-endpoint` on `data-refresh`,
for served pages) and `card_inline_html` (payload baked into the page as JSON and rendered
once — for the static, no-server dashboard the watchdog writes each tick).
"""

from __future__ import annotations

import html
import json as _json
import socket as _socket

SECTION_STYLE = (
    ".cpsection h2 .muted{font-weight:400;font-size:13px}"
    ".cpmetrics{display:flex;flex-wrap:wrap;gap:14px;margin:6px 0 12px}"
    ".cpm{min-width:96px}.cpm .k{font-size:11px;text-transform:uppercase;letter-spacing:.04em;opacity:.7}"
    ".cpm .v{font-size:17px;font-weight:650}"
    ".cprow{display:grid;grid-template-columns:64px 1fr;align-items:center;gap:8px}"
    ".cprow{margin:2px 0;font-size:12px}"
    ".cpbars{position:relative;height:16px}"
    ".cpbar{position:absolute;top:2px;height:5px;border-radius:2px}"
    ".cpbar.s1{top:9px;height:4px;opacity:.85}"
    ".cppos{background:#1a7f37}.cpneg{background:#cf222e}.cpvol{background:#9a6700}"
    ".cpaccent{color:#0969da;font-weight:650}.cprowfocus>div:first-child{color:#0969da;font-weight:650}"
    ".cperr{color:#9a6700}"
)

# Generic client renderer: finds every [data-cp-section] card, polls its data-endpoint, and renders the
# declarative payload (metrics tiles + a zero-centred, signed bar chart). Route-agnostic — the endpoint
# is read from the card's data attribute — so it never hardcodes a section id or URL.
SECTION_JS = r"""
(function(){
  function fmt(v){ if(v==null||isNaN(v)) return '0'; var a=Math.abs(v), s=v<0?'-':'';
    if(a>=1e9) return s+(a/1e9).toFixed(2)+'B'; if(a>=1e6) return s+(a/1e6).toFixed(1)+'M';
    if(a>=1e3) return s+(a/1e3).toFixed(0)+'K'; return ''+Math.round(v); }
  function tile(m){ var c=({pos:'cppos',neg:'cpneg',accent:'cpaccent',vol:'cpvol'})[m.tone]||'';
    return '<div class="cpm"><div class="k">'+m.label+'</div><div class="v '+c+'">'+m.value+'</div></div>'; }
  function toneClass(t){ return ({pos:'cppos',neg:'cpneg',accent:'cpaccent',vol:'cpvol'})[t]||''; }
  function renderBars(bars){
    var labels=bars.labels||[]; if(!labels.length) return '';
    var series=(bars.series||[]).slice(0,2), focus=bars.focus;
    // window to 21 rows around focus so near-the-focus structure stays visible
    var idx=labels.map(function(_,i){return i;});
    if(focus!=null && labels.length>21){
      var ci=0,best=1e18;
      for(var i=0;i<labels.length;i++){var dd=Math.abs(labels[i]-focus); if(dd<best){best=dd;ci=i;}}
      var lo=Math.max(0,ci-10); idx=idx.slice(lo,lo+21);
    }
    var mx=1;
    idx.forEach(function(i){ series.forEach(function(se){ mx=Math.max(mx,Math.abs(se.values[i]||0)); }); });
    var near=null;
    if(focus!=null){ var b2=1e18;
      idx.forEach(function(i){var d=Math.abs(labels[i]-focus); if(d<b2){b2=d;near=i;}}); }
    return idx.map(function(i){
      var bars_html = series.map(function(se,si){
        var v=se.values[i]||0, w=Math.min(50,Math.abs(v)/mx*50), left=v>=0?50:(50-w);
        var cls=se.tone_by_sign?(v>=0?'cppos':'cpneg'):(toneClass(se.tone)||'cpaccent');
        return '<div class="cpbar '+(si===1?'s1 ':'')+cls+'" style="left:'+left+'%;width:'+w+'%"></div>';
      }).join('');
      return '<div class="cprow'+(i===near?' cprowfocus':'')+'"><div>'+labels[i]+'</div>'
           + '<div class="cpbars">'+bars_html+'</div></div>';
    }).join('');
  }
  function cssColor(name, fallback){
    var v=getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v&&v.trim())||fallback; }
  function toneColor(t){
    var m={pos:cssColor('--pos','#1a7f37'),neg:cssColor('--neg','#cf222e'),
           accent:cssColor('--accent','#0969da'),vol:cssColor('--warn','#9a6700')};
    return m[t]||m.accent; }
  // Date-axis canvas chart: DPR-aware, nulls BREAK lines (a data gap must look like a
  // gap), optional dashed right-axis overlay + vertical markers. No chart library —
  // a loopback/static page must not reach a CDN (the flies rule, promoted suite-wide).
  function renderTimeseries(host, ts){
    host.innerHTML='';
    if(!ts) return;
    var labels=ts.labels||[]; if(!labels.length) return;
    var cv=document.createElement('canvas'); host.appendChild(cv);
    var W=host.clientWidth||600, H=180, dpr=window.devicePixelRatio||1;
    cv.width=W*dpr; cv.height=H*dpr; cv.style.width=W+'px'; cv.style.height=H+'px';
    var g=cv.getContext('2d'); g.scale(dpr,dpr);
    var padL=48, padR=ts.overlay?46:10, padT=10, padB=20;
    var ih=H-padT-padB, iw=W-padL-padR;
    var series=(ts.series||[]).slice(0,4);
    var lo=0, hi=0;
    series.forEach(function(se){ (se.values||[]).forEach(function(v){
      if(v!=null){ lo=Math.min(lo,v); hi=Math.max(hi,v); } }); });
    if(hi===lo) hi=lo+1;
    var span=hi-lo; lo-=span*0.05; hi+=span*0.05;
    var x=function(i){ return padL+(labels.length===1?iw/2:i/(labels.length-1)*iw); };
    var y=function(v){ return padT+(hi-v)/(hi-lo)*ih; };
    var muted=cssColor('--muted','#888');
    g.font='10px system-ui'; g.fillStyle=muted; g.strokeStyle=muted;
    for(var t=0;t<=4;t++){ var gv=lo+(hi-lo)*t/4, yy=y(gv);
      g.globalAlpha=0.15; g.beginPath(); g.moveTo(padL,yy); g.lineTo(W-padR,yy); g.stroke();
      g.globalAlpha=0.8; g.fillText(fmt(gv), 4, yy+3); }
    if(lo<0&&hi>0){ g.globalAlpha=0.4; g.beginPath(); g.moveTo(padL,y(0)); g.lineTo(W-padR,y(0)); g.stroke(); }
    g.globalAlpha=1;
    [0, labels.length>>1, labels.length-1].forEach(function(i){
      if(i<labels.length) g.fillText(String(labels[i]).slice(5), x(i)-13, H-6); });
    (ts.markers||[]).forEach(function(m){ var i=labels.indexOf(m.at); if(i<0) return;
      g.save(); g.strokeStyle=toneColor('vol'); g.setLineDash([3,3]);
      g.beginPath(); g.moveTo(x(i),padT); g.lineTo(x(i),H-padB); g.stroke();
      g.fillStyle=toneColor('vol'); g.fillText(m.label||'', x(i)+3, padT+9); g.restore(); });
    series.forEach(function(se){ g.strokeStyle=toneColor(se.tone); g.lineWidth=1.6;
      g.beginPath(); var pen=false;
      (se.values||[]).forEach(function(v,i){ if(v==null){pen=false;return;}
        if(pen){ g.lineTo(x(i),y(v)); } else { g.moveTo(x(i),y(v)); pen=true; } });
      g.stroke(); });
    if(ts.overlay&&(ts.overlay.values||[]).some(function(v){return v!=null;})){
      var ov=ts.overlay.values, olo=Infinity, ohi=-Infinity;
      ov.forEach(function(v){ if(v!=null){ olo=Math.min(olo,v); ohi=Math.max(ohi,v); } });
      if(ohi===olo) ohi=olo+1;
      var os=ohi-olo; olo-=os*0.1; ohi+=os*0.1;
      var oy=function(v){ return padT+(ohi-v)/(ohi-olo)*ih; };
      g.save(); g.strokeStyle=toneColor('vol'); g.setLineDash([4,3]); g.lineWidth=1.2;
      g.beginPath(); var pen2=false;
      ov.forEach(function(v,i){ if(v==null){pen2=false;return;}
        if(pen2){ g.lineTo(x(i),oy(v)); } else { g.moveTo(x(i),oy(v)); pen2=true; } });
      g.stroke();
      g.fillStyle=toneColor('vol'); g.setLineDash([]);
      g.fillText(ts.overlay.name||'', W-padR+3, padT+9);
      g.restore(); }
    if(series.length>1){ var lx=padL;
      series.forEach(function(se){ g.fillStyle=toneColor(se.tone);
        g.fillRect(lx,padT+1,8,3); g.fillStyle=muted;
        g.fillText(se.name||'', lx+11, padT+6);
        lx+=11+(se.name||'').length*5.4+14; }); }
  }
  function render(card, d){
    var sub=card.querySelector('.cpsub'), met=card.querySelector('.cpmetrics');
    var ch=card.querySelector('.cpchart'), note=card.querySelector('.cpnote');
    var tsh=card.querySelector('.cpts');
    if(!d||!d.ok){ sub.className='cpsub cperr'; sub.textContent=(d&&d.error)?d.error:'no data';
      met.innerHTML=''; ch.innerHTML=''; if(tsh) tsh.innerHTML=''; if(note) note.textContent=''; return; }
    sub.className='cpsub muted'; sub.textContent=d.subtitle||'';
    if(d.title){ card.querySelector('h2').childNodes[0].nodeValue=d.title+' '; }
    met.innerHTML=(d.metrics||[]).map(tile).join('');
    ch.innerHTML=d.bars?renderBars(d.bars):'';
    if(tsh) renderTimeseries(tsh, d.timeseries||null);
    if(note) note.textContent=d.note||'';
  }
  function wire(card){
    // Inline mode: the payload is baked into the page (the static dashboard) — render
    // once, no polling, no fetch. Endpoint mode: poll data-endpoint on data-refresh.
    var inline=card.querySelector('script.cpdata');
    if(inline){ try{ render(card, JSON.parse(inline.textContent)); }catch(e){} return; }
    var url=card.getAttribute('data-endpoint'), refresh=(+card.getAttribute('data-refresh')||15)*1000;
    function tick(){ fetch(url).then(function(r){return r.json();})
      .then(function(d){render(card,d);}).catch(function(){}); }
    tick(); setInterval(tick, refresh);
  }
  document.querySelectorAll('[data-cp-section]').forEach(wire);
})();
"""


def card_skeleton_html(section_id: str, title: str, endpoint: str, refresh: int = 15) -> str:
    """The static card skeleton the umbrella injects per enabled section; `SECTION_JS` fills it live.

    `endpoint` is the URL the card polls (the umbrella owns the route naming); the renderer reads it
    from the data attribute, so this module stays route-agnostic.
    """
    sid = html.escape(section_id)
    return (
        f'<section class="card cpsection" data-cp-section="{sid}" '
        f'data-endpoint="{html.escape(endpoint)}" data-refresh="{int(refresh)}">'
        f"<h2>{html.escape(title)} <span class=\"cpsub muted\">loading…</span></h2>"
        '<div class="cpmetrics"></div><div class="cpchart"></div><div class="cpts"></div>'
        '<div class="meta"><span class="cpnote muted"></span></div></section>'
    )


def card_inline_html(section_id: str, title: str, payload: dict) -> str:
    """A card with its payload BAKED IN (a `<script type="application/json">` block the
    renderer parses and draws once, no fetch) — how the static, no-server dashboard the
    watchdog writes each tick gets real charts while staying fully self-contained.
    The one JSON escape that matters inside a script element is `</`, which would
    otherwise close the tag mid-payload."""
    sid = html.escape(section_id)
    data = _json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    return (
        f'<section class="card cpsection" data-cp-section="{sid}">'
        f"<h2>{html.escape(title)} <span class=\"cpsub muted\"></span></h2>"
        f'<script type="application/json" class="cpdata">{data}</script>'
        '<div class="cpmetrics"></div><div class="cpchart"></div><div class="cpts"></div>'
        '<div class="meta"><span class="cpnote muted"></span></div></section>'
    )


def port_in_use(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    """Probe before binding, so a second dashboard launch reuses the running server instead of
    dying on EADDRINUSE — the port is a dashboard's singleton. The audit counted three copies of
    this probe across the module dashboards (gex, flies, meic); this is the one. The watchdog's
    reachability check deliberately stays its own: it lives on the reliability path (stdlib-only
    rule) and probes remote liveness with a 3s timeout, not local bind-ownership."""
    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, int(port))) == 0


def fmt_money(value, none: str = "—") -> str:
    """The suite's one money formatter (the audit counted seven hand-rolled copies):
    thousands-separated, two decimals, sign OUTSIDE the dollar sign (-$1,234.56)."""
    if value is None:
        return none
    try:
        v = float(value)
    except (TypeError, ValueError):
        return none
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"
