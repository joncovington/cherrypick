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


# Drag-to-reorder for dashboard cards — the suite's one copy (the audit counted three).
# Donor semantics are the orchestrator's 3-group version: grip-handle drag source (toggling a
# card's draggable on mousedown is unreliable in Chrome), geometric drop targeting, per-browser
# persistence, unknown-keys-append (a card shipped after a layout was saved must not vanish),
# and a deferred-drop mode for heavy children like iframes (moving one mid-drag reloads it).
#
# DOM contract (all attributes, no page-specific code):
#   [data-cp-reorder="<group-key>"]        a reorderable group; the value is its stable store key
#   data-cp-reorder-items="<selector>"     optional: which children are reorderable (default: all)
#   data-cp-reorder-label="<selector>"     optional: where a child's label lives (default: h2,h3)
#   data-cp-reorder-defer                  optional: apply the move once on drop, not live
#   [data-cp-reorder-store="<ls-key>"]     on any element: the page's localStorage key
#   #reset-layout                          optional button; shown (.show) once a layout is saved
REORDER_STYLE = (
    ".cp-reorder-item{position:relative}"
    ".reorder-handle{position:absolute;top:6px;right:6px;z-index:3;cursor:grab;"
    "color:var(--muted,#888);opacity:0;font-size:14px;line-height:1;padding:2px 5px;"
    "border:1px solid transparent;border-radius:4px;user-select:none;transition:opacity .15s}"
    ".cp-reorder-item:hover>.reorder-handle{opacity:.9}"
    ".reorder-handle:hover{opacity:1;border-color:var(--accent,#0969da)}"
    ".reorder-handle:active{cursor:grabbing}"
    ".reorder-drag{opacity:.35}"
    ".reorder-over{outline:1px dashed var(--accent,#0969da);outline-offset:-2px}"
    ".reset-layout{display:none}.reset-layout.show{display:inline-block}"
)

REORDER_JS = r"""
(function(){
  var groups=[].slice.call(document.querySelectorAll('[data-cp-reorder]'));
  if(!groups.length) return;
  var ksEl=document.querySelector('[data-cp-reorder-store]');
  var LS_KEY=(ksEl&&ksEl.getAttribute('data-cp-reorder-store'))||'cp-layout-v1';
  var store; try{store=JSON.parse(localStorage.getItem(LS_KEY))||{};}catch(e){store={};}
  function persist(){try{localStorage.setItem(LS_KEY,JSON.stringify(store));}catch(e){}}
  function slug(s){return (s||'').trim().toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'');}
  var srcOrder={},dragged=null,dragGroup=null;
  function managed(g){var sel=g.getAttribute('data-cp-reorder-items');
    return [].slice.call(g.children).filter(function(c){
      return c.nodeType===1&&(!sel||c.matches(sel));});}
  function kids(g){return managed(g).filter(function(c){return c.hasAttribute('data-rkey');});}
  function keys(g){return kids(g).map(function(c){return c.getAttribute('data-rkey');});}
  function ensureKey(g,card,idx){
    if(card.getAttribute('data-rkey')) return;
    var lsel=g.getAttribute('data-cp-reorder-label')||'h2,h3';
    var lbl=card.querySelector(lsel);
    var label=lbl?((lbl.childNodes[0]&&lbl.childNodes[0].nodeValue)||lbl.textContent):'';
    var base=slug(label)||('idx-'+idx),k=base,n=2;
    while(document.querySelector('[data-rkey="'+k+'"]')) k=base+'-'+(n++);
    card.setAttribute('data-rkey',k);
  }
  // Move `node` before `ref` within group `g`; ref===null means "after the last managed item" —
  // NOT appendChild, which would land past unmanaged trailing children (e.g. a footer).
  function place(g,ref,node){
    if(ref===null){var items=kids(g),last=items[items.length-1];
      if(last&&last!==node) g.insertBefore(node,last.nextSibling);}
    else if(ref!==node) g.insertBefore(node,ref);
  }
  function dragAfter(g,x,y){
    var best=null,bestScore=Infinity,list=kids(g);
    for(var i=0;i<list.length;i++){
      var el=list[i]; if(el===dragged) continue;
      var r=el.getBoundingClientRect(),cx=r.left+r.width/2,cy=r.top+r.height/2,gap=r.height*0.5,before;
      if(y<cy-gap) before=true; else if(y>cy+gap) before=false; else before=x<cx;
      if(before){var s=(cy-y)*(cy-y)+(cx-x)*(cx-x); if(s<bestScore){bestScore=s;best=el;}}
    }
    return best;
  }
  function showReset(){var b=document.getElementById('reset-layout'); if(b) b.classList.add('show');}
  // Reorder to match `order`, inserting before a fixed anchor (the element after the last managed
  // item) so items stay in their region. Unknown keys append rather than drop — a card shipped
  // after the layout was saved must never disappear for someone with a stored layout.
  function applyOrder(g,order){
    if(!order||!order.length) return;
    var byKey={}; kids(g).forEach(function(c){byKey[c.getAttribute('data-rkey')]=c;});
    var cur=kids(g),anchor=cur.length?cur[cur.length-1].nextSibling:null;
    order.forEach(function(k){if(byKey[k]) g.insertBefore(byKey[k],anchor);});
    kids(g).forEach(function(c){if(order.indexOf(c.getAttribute('data-rkey'))<0) g.insertBefore(c,anchor);});
  }
  groups.forEach(function(g){
    var gk=g.getAttribute('data-cp-reorder')||'group';
    var live=!g.hasAttribute('data-cp-reorder-defer');
    managed(g).forEach(function(c,i){ensureKey(g,c,i);});
    kids(g).forEach(function(card){
      card.classList.add('cp-reorder-item');
      var h=document.createElement('span');
      h.className='reorder-handle'; h.title='Drag to reorder'; h.textContent='⠇';
      h.setAttribute('draggable','true');
      card.insertBefore(h,card.firstChild);
      h.addEventListener('dragstart',function(e){
        dragged=card; dragGroup=g; card.classList.add('reorder-drag');
        e.dataTransfer.effectAllowed='move';
        try{e.dataTransfer.setData('text/plain',card.getAttribute('data-rkey'));}catch(_){}
        try{e.dataTransfer.setDragImage(card,20,20);}catch(_){}
      });
      h.addEventListener('dragend',function(){
        card.classList.remove('reorder-drag');
        if(dragged===card){
          if(!live){
            [].forEach.call(g.querySelectorAll('.reorder-over'),function(el){el.classList.remove('reorder-over');});
            if(g.__drop!==undefined){ place(g,g.__drop,card); g.__drop=undefined; }
          }
          store[gk]=keys(g); persist(); showReset();
        }
        dragged=null; dragGroup=null;
      });
    });
    srcOrder[gk]=keys(g);
    g.addEventListener('dragover',function(e){
      if(!dragged||dragGroup!==g) return;
      e.preventDefault(); e.dataTransfer.dropEffect='move';
      var after=dragAfter(g,e.clientX,e.clientY);
      if(live){ place(g,after,dragged); }
      else {
        [].forEach.call(g.querySelectorAll('.reorder-over'),function(el){el.classList.remove('reorder-over');});
        g.__drop=after;
        if(after) after.classList.add('reorder-over');
      }
    });
    applyOrder(g,store[gk]);
  });
  if(Object.keys(store).length) showReset();
  var reset=document.getElementById('reset-layout');
  if(reset) reset.addEventListener('click',function(){
    groups.forEach(function(g){ applyOrder(g,srcOrder[g.getAttribute('data-cp-reorder')||'group']); });
    store={}; try{localStorage.removeItem(LS_KEY);}catch(e){}
    reset.classList.remove('show');
  });
})();
"""


# Calendar heatmap of daily net P&L — the suite's one copy (the audit counted two: MEIC's
# month-grid and flies' week-column calendar). The flies form is the donor because it is the
# honest one: Monday-anchored week COLUMNS, Mon–Fri only (weekends are absent, not blank), and
# an empty weekday cell means "no settled session" — a different thing from a flat day, which
# gets a neutral filled cell. Dates are parsed and stepped in UTC so a local timezone never
# shifts a session onto the wrong day.
CAL_HEAT_STYLE = (
    ".cpcal{display:flex;gap:8px;overflow-x:auto;padding-bottom:4px;align-items:flex-start}"
    ".cpcal-side{display:grid;grid-template-rows:14px repeat(5,16px);row-gap:3px;font-size:10px;"
    "color:var(--muted,#888);text-align:right}"
    ".cpcal-main{display:flex;flex-direction:column}"
    ".cpcal-months{display:flex;gap:3px;height:14px;margin-bottom:3px;font-size:10px;color:var(--muted,#888)}"
    ".cpcal-mon{width:16px;white-space:nowrap;overflow:visible;flex:none}"
    ".cpcal-weeks{display:flex;gap:3px}"
    ".cpcal-week{display:grid;grid-template-rows:repeat(5,16px);row-gap:3px}"
    ".cpcal-cell{width:16px;height:16px;border-radius:3px;background:rgba(128,128,128,.15)}"
    ".cpcal-legend{display:flex;gap:4px;align-items:center;font-size:10px;color:var(--muted,#888);"
    "margin-top:6px}.cpcal-legend .cpcal-cell{width:11px;height:11px}"
)

# window.cpCalHeat(hostEl, days, fmtMoney?) — days: [{date: 'YYYY-MM-DD', net_pnl, trades}].
# Returns false (host cleared) when there is nothing to draw, so the page can show its own
# empty-state message. fmtMoney is optional; tooltips fall back to a plain $ rendering.
CAL_HEAT_JS = r"""
window.cpCalHeat = function(el, days, fmtMoney){
  if(!el) return false;
  if(!days || !days.length){ el.innerHTML=''; return false; }
  var fmt = fmtMoney || function(v){ v=v||0;
    return (v<0?'-$':'$')+Math.abs(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}); };
  var byDate={}, max=1;
  days.forEach(function(d){ byDate[d.date]=d; max=Math.max(max,Math.abs(d.net_pnl||0)); });
  var MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  function parse(s){ return new Date(s+'T00:00:00Z'); }
  function weekday(dt){ return (dt.getUTCDay()+6)%7; }
  function iso(dt){ return dt.toISOString().slice(0,10); }
  function addDays(dt,n){ var c=new Date(dt); c.setUTCDate(c.getUTCDate()+n); return c; }
  var sorted=days.map(function(d){return d.date;}).sort();
  var first=addDays(parse(sorted[0]), -weekday(parse(sorted[0])));
  var last=parse(sorted[sorted.length-1]);
  var weeks=[];
  for(var wk=new Date(first); wk<=last; wk=addDays(wk,7)) weeks.push(new Date(wk));
  var months=weeks.map(function(wk,i){
    var m=wk.getUTCMonth();
    var label=(i===0||m!==weeks[i-1].getUTCMonth())?MON[m]:'';
    return '<span class="cpcal-mon">'+label+'</span>';
  }).join('');
  var grid=weeks.map(function(wk){
    var cells='';
    for(var r=0;r<5;r++){
      var dt=addDays(wk,r), key=iso(dt);
      if(dt<first||dt>last){ cells+='<div class="cpcal-cell" style="visibility:hidden"></div>'; continue; }
      var d=byDate[key];
      if(!d){ cells+='<div class="cpcal-cell" title="'+key+': no settled session"></div>'; continue; }
      var v=d.net_pnl||0, a=Math.min(1,Math.abs(v)/max)*0.85+0.15;
      var col=v>0?'rgba(63,185,80,'+a.toFixed(2)+')':v<0?'rgba(248,81,73,'+a.toFixed(2)+')':'#30363d';
      var n=d.trades!=null?(' ('+d.trades+' trade'+(d.trades!==1?'s':'')+')'):'';
      cells+='<div class="cpcal-cell" style="background:'+col+'" title="'+key+': '+fmt(v)+n+'"></div>';
    }
    return '<div class="cpcal-week">'+cells+'</div>';
  }).join('');
  el.innerHTML=
    '<div><div class="cpcal">'
    +'<div class="cpcal-side"><div></div><div>Mon</div><div></div><div>Wed</div><div></div><div>Fri</div></div>'
    +'<div class="cpcal-main"><div class="cpcal-months">'+months+'</div>'
    +'<div class="cpcal-weeks">'+grid+'</div></div></div>'
    +'<div class="cpcal-legend"><span>loss</span>'
    +'<div class="cpcal-cell" style="background:rgba(248,81,73,.8)"></div>'
    +'<div class="cpcal-cell"></div>'
    +'<div class="cpcal-cell" style="background:rgba(63,185,80,.8)"></div><span>gain</span></div></div>';
  return true;
};
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
