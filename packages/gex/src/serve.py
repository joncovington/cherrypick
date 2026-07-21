"""The module's own ``dashboard --serve`` — a localhost live GEX view (SpotGamma/MenthorQ-style).

A stdlib ``ThreadingHTTPServer`` (no framework, no auth, loopback-only) serving one self-contained page
that polls ``/api/gex`` and draws three tabs — **GEX** (net GEX by strike: OI vs volume, walls, gamma
flip, a live spot marker and intraday spot trail), **IV Skew** (call/put IV + open interest by strike),
and **Volume** (call/put/total volume by strike). This is full parity with MEIC's old in-dashboard GEX
view, off the same ``service.build_gex`` payload and the same ``cherrypick.core.gex`` math. Read-only:
every refresh just re-reads MEIC's stream cache (this module never writes to it or fetches live).
"""

from __future__ import annotations

import json
import threading
import webbrowser
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import service as _service

_PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cherrypick GEX — __SYMBOL__</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
*{box-sizing:border-box}
body{margin:0;background:#0a0d12;color:#e6edf3;font:14px/1.4 system-ui,Segoe UI,Roboto,sans-serif}
header{display:flex;align-items:baseline;gap:16px;padding:14px 24px;border-bottom:1px solid #1e2430;flex-wrap:wrap}
h1{font-size:18px;margin:0;font-weight:650}
.hsub{color:#6b7280;font-size:13px}
.hcd{margin-left:auto;color:#6b7280;font-size:12px}
/* GEX view (ported verbatim from MEIC's dashboard so the two render identically) */
.gex-view{overflow-y:auto;padding:0 0 24px}
.gex-section{padding:20px 24px 0}
.gex-section-title{font-size:15px;font-weight:700;color:#e6edf3;margin-bottom:4px;display:flex;align-items:center;gap:8px}
.gex-section-sub{font-size:11px;color:#6b7280;margin-bottom:14px}
.gex-row{display:grid;gap:16px;margin-bottom:16px}
.gex-row-2{grid-template-columns:1fr 1fr}
.gex-row-main{grid-template-columns:1fr 280px}
.gex-body{display:flex;align-items:flex-start}
.gex-tabs{display:flex;flex-direction:column;gap:2px;padding:12px 8px;flex:0 0 84px;
          border-right:1px solid #1e2430;align-self:stretch}
.gex-tab{font-size:11px;font-weight:700;color:#6b7280;padding:8px 10px;cursor:pointer;
         border-right:2px solid transparent;margin-right:-1px;text-transform:uppercase;
         letter-spacing:.8px;transition:color .15s,border-color .15s;border-radius:4px 0 0 4px}
.gex-tab:hover{color:#e6edf3}
.gex-tab.active{color:#00c896;border-right-color:#00c896;background:#0d2018}
.gex-tab-panels{flex:1;min-width:0}
.gex-tab-panel{display:none}.gex-tab-panel.active{display:block}
.chart-card{background:#0d1117;border:1px solid #1e2430;border-radius:6px;padding:14px 16px}
.chart-card-title{font-size:10px;font-weight:700;color:#6b7280;letter-spacing:1.2px;
                   text-transform:uppercase;margin-bottom:10px}
.chart-card canvas{display:block;width:100%!important}
.radio-group{display:flex;gap:4px;margin-bottom:10px}
.radio-group label{display:flex;align-items:center;gap:5px;cursor:pointer;
                    font-size:11px;color:#6b7280;padding:4px 10px;
                    border:1px solid #1e2430;border-radius:4px;transition:all .15s}
.radio-group label:hover{color:#e6edf3;border-color:#3d4451}
.radio-group input{display:none}
.radio-group input:checked+span{color:#e6edf3}
.radio-group label:has(input:checked){color:#e6edf3;border-color:#00c896;background:#0d2018}
.metrics-panel{background:#0d1117;border:1px solid #1e2430;border-radius:6px;padding:16px}
.metrics-panel-title{font-size:10px;font-weight:700;color:#6b7280;letter-spacing:1.2px;
                      text-transform:uppercase;margin-bottom:14px;display:flex;align-items:center;gap:6px}
.metric-row{margin-bottom:14px}
.metric-lbl{font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:.8px;margin-bottom:2px}
.metric-val{font-size:22px;font-weight:700;color:#e6edf3;line-height:1.1}
.metric-val.pos{color:#00c896}.metric-val.neg{color:#e8423a}
.metric-divider{height:1px;background:#1e2430;margin:10px 0}
.gex-symbol-select{background:#0d1117;color:#e6edf3;border:1px solid #1e2430;border-radius:4px;
                   padding:4px 8px;font-size:12px;cursor:pointer;outline:none}
</style></head>
<body>
<header>
  <h1>Cherrypick GEX</h1>
  <span class="hsub" id="gex-main-sub">loading…</span>
  <span class="hcd" id="scountdown"></span>
</header>

<div class="gex-view" id="gex-inner">
  <div class="gex-body">
    <div class="gex-tabs">
      <div class="gex-tab active" data-gex-tab="gex">GEX</div>
      <div class="gex-tab" data-gex-tab="ivskew">IV Skew</div>
      <div class="gex-tab" data-gex-tab="volume">Volume</div>
    </div>
    <div class="gex-tab-panels">

    <!-- Tab: GEX -->
    <div class="gex-tab-panel active" id="gex-panel-gex">
      <div class="gex-section">
        <div class="gex-row gex-row-main">
          <div class="chart-card">
            <div class="chart-card-title" id="gex-chart-title">GEX by Strike — Net GEX</div>
            <div style="position:relative;height:260px"><canvas id="gex-main-chart"></canvas></div>
          </div>
          <div>
            <div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:12px">
              <span style="font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:.8px">Symbol</span>
              <select id="gex-symbol-select" class="gex-symbol-select">__OPTIONS__</select>
              <span id="gex-source-badge" class="gex-source-badge" style="font-size:10px;color:#6b7280"></span>
            </div>
            <div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:12px">
              <span style="font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:.8px">GEX View</span>
              <div class="radio-group" id="gex-view-group">
                <label><input type="radio" name="gex_view" value="oivol"><span>OI vs Volume</span></label>
                <label><input type="radio" name="gex_view" value="net" checked><span>&#11044; Net GEX</span></label>
                <label><input type="radio" name="gex_view" value="abs"><span>Absolute</span></label>
              </div>
            </div>
            <div class="metrics-panel">
              <div class="metrics-panel-title">&#128202; OPEN INTEREST (POSITIONING)</div>
              <div class="metric-row"><div class="metric-lbl">Total Call GEX</div><div class="metric-val pos" id="m-call-gex">&mdash;</div></div>
              <div class="metric-row"><div class="metric-lbl">Total Put GEX</div><div class="metric-val neg" id="m-put-gex">&mdash;</div></div>
              <div class="metric-divider"></div>
              <div class="metric-row"><div class="metric-lbl">Net GEX</div><div class="metric-val" id="m-net-gex">&mdash;</div></div>
              <div class="metric-row"><div class="metric-lbl">Max GEX Strike</div><div class="metric-val" id="m-max-strike">&mdash;</div></div>
              <div class="metric-divider"></div>
              <div class="metric-row"><div class="metric-lbl">Call Wall <span title="Strike with the largest call-side gamma concentration — dealer resistance above spot" style="cursor:help;color:#3d4451">&#9432;</span></div><div class="metric-val pos" id="m-call-wall">&mdash;</div></div>
              <div class="metric-row"><div class="metric-lbl">Put Wall <span title="Strike with the largest put-side gamma concentration — dealer support below spot" style="cursor:help;color:#3d4451">&#9432;</span></div><div class="metric-val neg" id="m-put-wall">&mdash;</div></div>
              <div class="metric-divider"></div>
              <div class="metric-row" style="margin-bottom:0"><div class="metric-lbl">Zero Gamma (Flip) <span title="Strike where dealer GEX transitions from negative to positive" style="cursor:help;color:#3d4451">&#9432;</span></div><div class="metric-val" id="m-zero-gamma">&mdash;</div></div>
            </div>
            <div class="metrics-panel">
              <div class="metrics-panel-title">&#128200; VOLUME (FLOW)</div>
              <div class="metric-row"><div class="metric-lbl">Total Call GEX</div><div class="metric-val pos" id="m-call-gex-vol">&mdash;</div></div>
              <div class="metric-row"><div class="metric-lbl">Total Put GEX</div><div class="metric-val neg" id="m-put-gex-vol">&mdash;</div></div>
              <div class="metric-divider"></div>
              <div class="metric-row"><div class="metric-lbl">Net GEX</div><div class="metric-val" id="m-net-gex-vol">&mdash;</div></div>
              <div class="metric-divider"></div>
              <div class="metric-row"><div class="metric-lbl">Call Wall <span title="Strike with the largest call-side volume-gamma concentration" style="cursor:help;color:#3d4451">&#9432;</span></div><div class="metric-val pos" id="m-call-wall-vol">&mdash;</div></div>
              <div class="metric-row"><div class="metric-lbl">Put Wall <span title="Strike with the largest put-side volume-gamma concentration" style="cursor:help;color:#3d4451">&#9432;</span></div><div class="metric-val neg" id="m-put-wall-vol">&mdash;</div></div>
              <div class="metric-divider"></div>
              <div class="metric-row" style="margin-bottom:0"><div class="metric-lbl">Zero Gamma (Flip) <span title="Volume-basis strike where dealer GEX transitions from negative to positive" style="cursor:help;color:#3d4451">&#9432;</span></div><div class="metric-val" id="m-zero-gamma-vol">&mdash;</div></div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Tab: IV Skew -->
    <div class="gex-tab-panel" id="gex-panel-ivskew">
      <div class="gex-section">
        <div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:8px">
          <span style="font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:.8px">Symbol</span>
          <select id="gex-symbol-select-iv" class="gex-symbol-select">__OPTIONS__</select>
          <span id="gex-source-badge-iv" class="gex-source-badge" style="font-size:10px;color:#6b7280"></span>
        </div>
        <div class="gex-section-sub" id="gex-iv-sub">&nbsp;</div>
        <div class="gex-row gex-row-2">
          <div class="chart-card">
            <div class="chart-card-title">Call IV vs Put IV by Strike</div>
            <div style="position:relative;height:220px"><canvas id="gex-iv-chart"></canvas></div>
          </div>
          <div class="chart-card">
            <div class="chart-card-title">Open Interest by Strike</div>
            <div style="position:relative;height:220px"><canvas id="gex-oi-chart"></canvas></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Tab: Volume -->
    <div class="gex-tab-panel" id="gex-panel-volume">
      <div class="gex-section">
        <div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:8px">
          <span style="font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:.8px">Symbol</span>
          <select id="gex-symbol-select-vol" class="gex-symbol-select">__OPTIONS__</select>
          <span id="gex-source-badge-vol" class="gex-source-badge" style="font-size:10px;color:#6b7280"></span>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
          <div class="gex-section-title" style="margin:0">&#128200; Volume by Strike</div>
          <div class="radio-group" id="vol-view-group" style="margin-bottom:0">
            <label><input type="radio" name="vol_view" value="split"><span>Calls vs Puts</span></label>
            <label><input type="radio" name="vol_view" value="total" checked><span>&#11044; Total Volume</span></label>
          </div>
        </div>
        <div class="chart-card">
          <div style="position:relative;height:260px"><canvas id="gex-vol-chart"></canvas></div>
        </div>
      </div>
    </div>

    </div>
  </div>
</div>

<script>
// ── GEX (ported from MEIC's dashboard; standalone poll loop below) ─────────────
let gexData = null;
let gexIvChart = null, gexOiChart = null, gexVolChart = null, gexMainChart = null;
let cd = __REFRESH__;

function fGex(v){
  if(v==null) return '—';
  const abs=Math.abs(v), sign=v<0?'-':'';
  if(abs>=1e9) return sign+'$'+(abs/1e9).toFixed(2)+'B';
  if(abs>=1e6) return sign+'$'+(abs/1e6).toFixed(2)+'M';
  if(abs>=1e3) return sign+'$'+(abs/1e3).toFixed(1)+'K';
  return sign+'$'+abs.toFixed(0);
}

function _vline(x,label,color){
  return { id:'vline_'+label, beforeDatasetsDraw(chart){
    const {ctx,scales}=chart; if(!scales.x) return;
    const xPx=scales.x.getPixelForValue(x); if(xPx==null||isNaN(xPx)) return;
    const {top,bottom}=chart.chartArea;
    ctx.save(); ctx.setLineDash([4,4]); ctx.strokeStyle=color; ctx.lineWidth=1;
    ctx.beginPath(); ctx.moveTo(xPx,top); ctx.lineTo(xPx,bottom); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle=color; ctx.font='9px sans-serif'; ctx.textAlign='center';
    ctx.fillText(label,xPx,bottom+12); ctx.restore();
  }};
}

// Interpolate a pixel for a continuous price on a Chart.js category (strike) axis.
function _categoryPixelForValue(scale,labels,value){
  if(!labels||!labels.length) return null;
  const n=labels.length; if(n===1) return (scale.top+scale.bottom)/2;
  let loIdx;
  if(value<=labels[0]) loIdx=0;
  else if(value>=labels[n-1]) loIdx=n-2;
  else { loIdx=0; for(let i=0;i<n-1;i++){ if(value>=labels[i]&&value<=labels[i+1]){ loIdx=i; break; } } }
  const lo=labels[loIdx], hi=labels[loIdx+1];
  const frac=hi===lo?0:(value-lo)/(hi-lo);
  const logicalIdx=loIdx+frac;
  const top=scale.top, bottom=scale.bottom;
  if(top==null||bottom==null||isNaN(top)||isNaN(bottom)) return null;
  const reversed=!!(scale.options&&scale.options.reverse);
  const posFrac=logicalIdx/(n-1);
  return reversed?(bottom-posFrac*(bottom-top)):(top+posFrac*(bottom-top));
}

function _hline(y,label,color,opts){
  const solid=opts&&opts.solid, onLeft=opts&&opts.left, fill=opts&&opts.fill;
  return { id:'hline_'+label, beforeDatasetsDraw(chart){
    const {ctx,scales}=chart; if(!scales.y) return;
    let yPx=_categoryPixelForValue(scales.y,chart.data.labels,y);
    if(yPx==null||isNaN(yPx)) return;
    const {left,right,top,bottom}=chart.chartArea;
    yPx=Math.max(top,Math.min(bottom,yPx));
    ctx.save(); if(!solid) ctx.setLineDash([4,4]);
    ctx.strokeStyle=color; ctx.lineWidth=solid?1.25:1;
    ctx.beginPath(); ctx.moveTo(left,yPx); ctx.lineTo(right,yPx); ctx.stroke(); ctx.setLineDash([]);
    ctx.font='bold 10px sans-serif';
    const textW=ctx.measureText(label).width, padX=6, tagH=15;
    const tagX=onLeft?left+2:right-textW-padX*2, tagY=yPx-tagH/2;
    ctx.fillStyle=fill?color:'#0d1117'; ctx.beginPath();
    if(ctx.roundRect) ctx.roundRect(tagX,tagY,textW+padX*2,tagH,3); else ctx.rect(tagX,tagY,textW+padX*2,tagH);
    ctx.fill(); ctx.strokeStyle=color; ctx.lineWidth=1; ctx.stroke();
    ctx.fillStyle=fill?'#0d1117':color; ctx.textAlign='left'; ctx.textBaseline='middle';
    ctx.fillText(label,tagX+padX,yPx+1); ctx.restore();
  }};
}

function _trimToData(series,valueKeys,pad){
  let lo=-1,hi=-1;
  for(let i=0;i<series.length;i++){
    const nz=valueKeys.some(k=>Math.abs(series[i][k]||0)>0);
    if(nz){ if(lo===-1) lo=i; hi=i; }
  }
  if(lo===-1) return series;
  return series.slice(Math.max(0,lo-pad),Math.min(series.length,hi+pad+1));
}

function _fitChartToViewport(canvasId,bottomPad,minH){
  const canvas=document.getElementById(canvasId); const wrap=canvas&&canvas.parentElement;
  if(!wrap) return;
  const top=wrap.getBoundingClientRect().top;
  const avail=window.innerHeight-top-(bottomPad||24);
  wrap.style.height=Math.max(minH||220,avail)+'px';
}

function _baseOpts(plugins){
  return {
    responsive:true, maintainAspectRatio:false, animation:false,
    plugins:{ legend:{display:false},
      tooltip:{mode:'index',intersect:false,backgroundColor:'#1a1f2e',titleColor:'#e6edf3',
               bodyColor:'#8b949e',borderColor:'#1e2430',borderWidth:1}, ...(plugins||{}) },
    scales:{ x:{grid:{color:'#1a1f2a'},ticks:{color:'#4a5568',font:{size:9},maxRotation:0}},
             y:{grid:{color:'#1a1f2a'},ticks:{color:'#4a5568',font:{size:9}}} }
  };
}

function renderIvChart(series,spot){
  const labels=series.map(s=>s.strike);
  const ds=[
    {label:'Call IV',data:series.map(s=>s.call_iv||null),borderColor:'green',backgroundColor:'rgba(0,128,0,0.1)',pointRadius:4,pointHoverRadius:6,borderWidth:2,tension:0,fill:false},
    {label:'Put IV',data:series.map(s=>s.put_iv||null),borderColor:'red',backgroundColor:'rgba(255,0,0,0.1)',pointRadius:4,pointHoverRadius:6,borderWidth:2,tension:0,fill:false},
  ];
  const opts=_baseOpts();
  opts.scales.x.title={display:true,text:'Strike Price',color:'#6b7280'};
  opts.scales.y.title={display:true,text:'Implied Volatility (%)',color:'#6b7280'};
  opts.scales.y.ticks.callback=v=>v.toFixed(1)+'%';
  opts.plugins.tooltip.mode='index';
  opts.plugins.tooltip.callbacks={label:ctx=>ctx.dataset.label+': '+(ctx.parsed.y||0).toFixed(2)+'%'};
  opts.plugins.vline=spot!=null?_vline(spot,'$'+spot.toFixed(2),'#f5a623'):{};
  if(gexIvChart){ gexIvChart.data.labels=labels; gexIvChart.data.datasets=ds; gexIvChart.update(); return; }
  gexIvChart=new Chart(document.getElementById('gex-iv-chart'),{type:'line',data:{labels,datasets:ds},options:opts});
}

function renderOiChart(series,spot){
  series=_trimToData(series,['call_oi','put_oi','call_vol','put_vol'],3);
  const labels=series.map(s=>s.strike);
  const ds=[
    {label:'Call OI',data:series.map(s=>s.call_oi),backgroundColor:'green'},
    {label:'Put OI',data:series.map(s=>-s.put_oi),backgroundColor:'red'},
    {label:'Call Volume',data:series.map(s=>s.call_vol),backgroundColor:'lightgreen'},
    {label:'Put Volume',data:series.map(s=>-s.put_vol),backgroundColor:'lightcoral'},
  ];
  const opts=_baseOpts();
  opts.indexAxis='y';
  opts.interaction={mode:'index',intersect:false,axis:'y'};
  opts.scales.y.title={display:true,text:'Strike',color:'#6b7280'};
  opts.scales.y.reverse=true;
  opts.scales.x.title={display:true,text:'Open Interest / Volume',color:'#6b7280'};
  opts.scales.y.grouped=false;
  opts.plugins.tooltip.callbacks={label:ctx=>(ctx.dataset.label||'')+': '+Math.abs(ctx.parsed.x).toLocaleString()};
  if(gexOiChart){ gexOiChart.destroy(); gexOiChart=null; }
  gexOiChart=new Chart(document.getElementById('gex-oi-chart'),
    {type:'bar',data:{labels,datasets:ds},options:opts,
     plugins:spot!=null?[_hline(spot,'$'+spot.toFixed(2),'#00b4ff',{solid:true})]:[]});
}

function renderVolChart(series,spot,mode){
  series=_trimToData(series,['call_vol','put_vol','total_vol'],3);
  const labels=series.map(s=>s.strike);
  let ds;
  if(mode==='split'){
    ds=[
      {label:'Call Volume',data:series.map(s=>s.call_vol),backgroundColor:'lightgreen'},
      {label:'Put Volume',data:series.map(s=>-s.put_vol),backgroundColor:'lightcoral'},
    ];
  } else {
    ds=[{label:'Total Volume',data:series.map(s=>s.total_vol),backgroundColor:'purple'}];
  }
  const opts=_baseOpts();
  opts.indexAxis='y';
  opts.interaction={mode:'index',intersect:false,axis:'y'};
  opts.scales.y.title={display:true,text:'Strike',color:'#6b7280'};
  opts.scales.y.reverse=true;
  opts.scales.x.title={display:true,text:'Volume',color:'#6b7280'};
  if(mode==='split'){ opts.scales.x.stacked=true; opts.scales.y.stacked=true; }
  opts.plugins.tooltip.callbacks={label:ctx=>(ctx.dataset.label||'')+': '+Math.abs(ctx.parsed.x).toLocaleString()};
  if(gexVolChart){ gexVolChart.destroy(); gexVolChart=null; }
  gexVolChart=new Chart(document.getElementById('gex-vol-chart'),
    {type:'bar',data:{labels,datasets:ds},options:opts,
     plugins:spot!=null?[_hline(spot,'$'+spot.toFixed(2),'#00b4ff',{solid:true})]:[]});
}

// Trace the day's spot price as a light-blue line over the GEX profile: Y from the strike-axis
// category interpolation, X from wall-clock time (market open -> close).
function _spotHistoryPlugin(history,labels,openTs,closeTs){
  return { id:'spotHistory', afterDatasetsDraw(chart){
    const {ctx,scales,chartArea}=chart;
    if(!scales.y||!history||!history.length) return;
    if(openTs==null||closeTs==null||closeTs<=openTs) return;
    const pts=[];
    for(const pt of history){
      const frac=(pt.ts-openTs)/(closeTs-openTs);
      if(frac<0||frac>1) continue;
      let yPx=_categoryPixelForValue(scales.y,labels,pt.spot);
      if(yPx==null||isNaN(yPx)) continue;
      yPx=Math.max(chartArea.top,Math.min(chartArea.bottom,yPx));
      const xPx=chartArea.left+frac*(chartArea.right-chartArea.left);
      pts.push([xPx,yPx]);
    }
    if(!pts.length) return;
    ctx.save(); ctx.strokeStyle='#7ec8f2'; ctx.lineWidth=1.5; ctx.beginPath();
    pts.forEach(([x,y],i)=>{ i===0?ctx.moveTo(x,y):ctx.lineTo(x,y); }); ctx.stroke();
    ctx.fillStyle='#7ec8f2';
    pts.forEach(([x,y])=>{ ctx.beginPath(); ctx.arc(x,y,1.5,0,Math.PI*2); ctx.fill(); });
    ctx.restore();
  }};
}

function renderGexMainChart(series,spot,zero,mode,callWall,putWall,spotHistory,marketOpenTs,marketCloseTs){
  series=_trimToData(series,['call_gex','put_gex','net_gex','abs_gex','net_gex_vol'],3);
  const labels=series.map(s=>s.strike);
  let ds,titleText;
  if(mode==='oivol'){
    ds=[
      {label:'Net GEX (OI)',data:series.map(s=>s.net_gex),backgroundColor:series.map(s=>s.net_gex>=0?'green':'red')},
      {label:'Net GEX (Volume)',data:series.map(s=>s.net_gex_vol),backgroundColor:series.map(s=>s.net_gex_vol>=0?'lightgreen':'lightcoral')},
    ];
    titleText='GEX by Strike — Net GEX (OI vs Volume)';
  } else if(mode==='abs'){
    ds=[{label:'|Net GEX|',data:series.map(s=>s.abs_gex),backgroundColor:'blue'}];
    titleText='GEX by Strike — Absolute GEX';
  } else {
    ds=[{label:'Net GEX',data:series.map(s=>s.net_gex),backgroundColor:series.map(s=>s.net_gex>=0?'green':'red')}];
    titleText='GEX by Strike — Net GEX (Green=Call Heavy, Red=Put Heavy)';
  }
  document.getElementById('gex-chart-title').textContent=titleText;
  const opts=_baseOpts();
  opts.indexAxis='y';
  opts.interaction={mode:'index',intersect:false,axis:'y'};
  opts.scales.y.title={display:true,text:'Strike Price',color:'#6b7280'};
  opts.scales.y.reverse=true;
  opts.scales.x.title={display:true,text:'Gamma Exposure ($)',color:'#6b7280'};
  opts.scales.x.ticks.callback=v=>fGex(v);
  opts.plugins.tooltip.callbacks={label:ctx=>(ctx.dataset.label||'')+': '+fGex(ctx.parsed.x)};
  opts.datasets={bar:{barThickness:6,maxBarThickness:8}};  // dominant green/red bars
  _fitChartToViewport('gex-main-chart',24,220);
  const hlinePlugins=[];
  if(spot!=null) hlinePlugins.push(_hline(spot,'$'+spot.toFixed(2),'#00b4ff',{solid:true}));
  if(zero!=null) hlinePlugins.push(_hline(zero,'Zero Γ: $'+zero.toFixed(2),'#e8b923'));
  if(callWall!=null) hlinePlugins.push(_hline(callWall,callWall.toFixed(2),'#21ce3c',{left:true,fill:true}));
  if(putWall!=null) hlinePlugins.push(_hline(putWall,putWall.toFixed(2),'#e8423a',{left:true,fill:true}));
  opts.plugins.customHlines={id:'customHlines',beforeDatasetsDraw(chart){ hlinePlugins.forEach(p=>p.beforeDatasetsDraw(chart)); }};
  if(gexMainChart){ gexMainChart.destroy(); gexMainChart=null; }
  gexMainChart=new Chart(document.getElementById('gex-main-chart'),
    {type:'bar',data:{labels,datasets:ds},options:opts,
     plugins:[opts.plugins.customHlines,_spotHistoryPlugin(spotHistory,labels,marketOpenTs,marketCloseTs)]});
}

function renderGexMetrics(totals){
  const t=totals||{};
  document.getElementById('m-call-gex').textContent=fGex(t.total_call_gex);
  document.getElementById('m-put-gex').textContent=t.total_put_gex!=null?fGex(-t.total_put_gex):'—';
  const netEl=document.getElementById('m-net-gex');
  netEl.textContent=fGex(t.net_gex); netEl.className='metric-val '+(t.net_gex>=0?'pos':'neg');
  document.getElementById('m-max-strike').textContent=t.max_gex_strike!=null?'$'+t.max_gex_strike:'—';
  document.getElementById('m-call-wall').textContent=t.call_wall!=null?'$'+t.call_wall:'—';
  document.getElementById('m-put-wall').textContent=t.put_wall!=null?'$'+t.put_wall:'—';
  document.getElementById('m-zero-gamma').textContent=t.zero_gamma!=null?'$'+t.zero_gamma.toFixed(2):'—';
  document.getElementById('m-call-gex-vol').textContent=fGex(t.total_call_gex_vol);
  document.getElementById('m-put-gex-vol').textContent=t.total_put_gex_vol!=null?fGex(-t.total_put_gex_vol):'—';
  const netVolEl=document.getElementById('m-net-gex-vol');
  netVolEl.textContent=fGex(t.net_gex_vol); netVolEl.className='metric-val '+(t.net_gex_vol>=0?'pos':'neg');
  document.getElementById('m-call-wall-vol').textContent=t.call_wall_vol!=null?'$'+t.call_wall_vol:'—';
  document.getElementById('m-put-wall-vol').textContent=t.put_wall_vol!=null?'$'+t.put_wall_vol:'—';
  document.getElementById('m-zero-gamma-vol').textContent=t.zero_gamma_vol!=null?'$'+t.zero_gamma_vol.toFixed(2):'—';
}

function renderGex(d){
  gexData=d;
  if(!d.ok){ document.getElementById('gex-iv-sub').textContent=d.error||'No data'; return; }
  const series=d.series||[];
  const spot=d.underlying_price;
  const zero=d.totals&&d.totals.zero_gamma_vol;
  const callWall=d.totals&&d.totals.call_wall_vol;
  const putWall=d.totals&&d.totals.put_wall_vol;
  const spotHistory=d.spot_history||[];
  const sym=d.symbol||'', exp=d.expiration||'';
  document.getElementById('gex-iv-sub').textContent=sym+' Implied Volatility Skew — Exp: '+exp;
  document.getElementById('gex-main-sub').textContent=sym+' — Exp: '+exp+(spot?'  |  Spot: $'+spot.toFixed(2):'');
  const gexMode=document.querySelector('input[name="gex_view"]:checked')?.value||'net';
  const volMode=document.querySelector('input[name="vol_view"]:checked')?.value||'total';
  renderIvChart(series,spot);
  renderOiChart(series,spot);
  renderVolChart(series,spot,volMode);
  renderGexMainChart(series,spot,zero,gexMode,callWall,putWall,spotHistory,d.market_open_ts,d.market_close_ts);
  renderGexMetrics(d.totals);
}

function gexSymbol(){
  const sel=document.querySelector('.gex-symbol-select');
  if(!sel) return '';
  return sel.value||(sel.options.length?sel.options[0].value:'');
}

function _setGexBadges(text,color){
  document.querySelectorAll('.gex-source-badge').forEach(b=>{ b.textContent=text; b.style.color=color||'#6b7280'; });
}

async function fetchGex(){
  const sym=gexSymbol(); if(!sym) return;
  _setGexBadges('Loading…');
  try{
    const r=await fetch('/api/gex?symbol='+encodeURIComponent(sym));
    if(!r.ok){ _setGexBadges('HTTP '+r.status,'#e8423a'); return; }
    const d=await r.json();
    if(!d.ok){ _setGexBadges('error: '+(d.error||'unknown'),'#e8423a'); renderGex(d); return; }
    _setGexBadges(d.source==='stream_cache'?'● stream cache':(d.source||''), d.source==='stream_cache'?'#00c896':'#6b7280');
    renderGex(d);
  }catch(_){ _setGexBadges('error','#e8423a'); }
}

// GEX sub-tabs
document.querySelectorAll('.gex-tab').forEach(tab=>{
  tab.addEventListener('click',()=>{
    document.querySelectorAll('.gex-tab').forEach(t=>t.classList.remove('active'));
    document.querySelectorAll('.gex-tab-panel').forEach(p=>p.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('gex-panel-'+tab.dataset.gexTab).classList.add('active');
    if(tab.dataset.gexTab==='gex'&&gexData) _fitChartToViewport('gex-main-chart',24,220);
    [gexMainChart,gexIvChart,gexOiChart,gexVolChart].forEach(c=>{ if(c) c.resize(); });
  });
});

window.addEventListener('resize',()=>{
  if(!gexMainChart) return;
  _fitChartToViewport('gex-main-chart',24,220); gexMainChart.resize();
});

// keep the three symbol selectors in sync; refetch on change
document.querySelectorAll('.gex-symbol-select').forEach(sel=>{
  sel.addEventListener('change',()=>{
    const sym=sel.value;
    document.querySelectorAll('.gex-symbol-select').forEach(o=>{ o.value=sym; });
    fetchGex();
  });
});

document.querySelectorAll('input[name="gex_view"]').forEach(el=>
  el.addEventListener('change',()=>{ if(gexData) renderGex(gexData); }));
document.querySelectorAll('input[name="vol_view"]').forEach(el=>
  el.addEventListener('change',()=>{ if(gexData) renderGex(gexData); }));

// Live push (primary) + polling (fallback while the socket is not live).
let wsLive=false, wsBackoff=1000, ws=null;
function _pollTick(){
  if(wsLive) return;                 // socket owns updates while live
  cd--; document.getElementById('scountdown').textContent='Refresh in '+cd+'s';
  if(cd<=0){ cd=__REFRESH__; fetchGex(); }
}
function openWs(){
  const proto=location.protocol==='https:'?'wss':'ws';
  ws=new WebSocket(proto+'://'+location.hostname+':__WSPORT__/');
  let gotMsg=false;
  ws.onopen=()=>{ ws.send(JSON.stringify({symbol:gexSymbol()})); };
  ws.onmessage=(e)=>{
    let d; try{ d=JSON.parse(e.data); }catch(_){ return; }
    if(!gotMsg){ gotMsg=true; wsLive=true; wsBackoff=1000;
      _setGexBadges('● live','#00c896'); }
    gexData=d; renderGex(d);
  };
  ws.onclose=()=>{ wsLive=false; _setGexBadges('reconnecting…','#6b7280');
    setTimeout(openWs,wsBackoff);
    wsBackoff=Math.min(wsBackoff*2,30000); };
}
// symbol dropdown: tell the socket, and keep the fallback path warm
document.querySelectorAll('.gex-symbol-select').forEach(sel=>{
  sel.addEventListener('change',()=>{
    if(ws&&ws.readyState===1) ws.send(JSON.stringify({symbol:sel.value}));
  });
});
fetchGex();               // instant first paint via HTTP
setInterval(_pollTick,1000);
openWs();
</script>
</body></html>"""


def _render_page(symbol: str, refresh: int, symbols: list[str], ws_port_num: int = 5056) -> bytes:
    opts = "".join(
        f'<option value="{escape(s)}"{" selected" if s == symbol else ""}>{escape(s)}</option>'
        for s in symbols
    )
    html = (
        _PAGE.replace("__SYMBOL__", escape(symbol))
        .replace("__REFRESH__", str(refresh))
        .replace("__WSPORT__", str(ws_port_num))
        .replace("__OPTIONS__", opts)
    )
    return html.encode("utf-8")


def make_handler(cfg: dict, default_sym: str):
    from config import ws_port

    refresh = int(cfg["serve"].get("refresh_seconds", 15))
    symbols = [str(s).upper() for s in (cfg.get("symbols") or [default_sym])]
    if default_sym not in symbols:
        symbols = [default_sym, *symbols]
    ws_port_num = ws_port(cfg)

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
                self._send(
                    200,
                    _render_page(default_sym, refresh, symbols, ws_port_num),
                    "text/html; charset=utf-8",
                )
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

    # Background spot-trail recorder: sample EVERY offered symbol's spot on the refresh cadence (not just
    # the one on screen), so each symbol's trail stays continuous and there's no gap when the viewer
    # switches symbols. Runs once per server; the per-request build_gex only reads the trail.
    refresh = int(cfg["serve"].get("refresh_seconds", 15))
    stop = threading.Event()

    def _record_loop():
        while not stop.is_set():
            try:
                _service.record_spots(cfg)
            except Exception:  # a data hiccup must never kill the recorder
                pass
            stop.wait(refresh)

    threading.Thread(target=_record_loop, name="gex-spot-recorder", daemon=True).start()

    from config import ws_port as _ws_port
    from push import GexPushServer
    push_srv = GexPushServer(cfg)
    threading.Thread(target=push_srv.start, args=(host,),
                     name="gex-push", daemon=True).start()
    print(f"cherrypick-gex push serving at ws://{host}:{_ws_port(cfg)}/")

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
