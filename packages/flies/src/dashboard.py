"""Read-only dashboard for cherrypick-flies — loopback HTTP, no build step.

Mirrors `packages/meic/src/dashboard.py`: one stdlib `http.server`, one HTML string, two routes
(`/` and `/api/data`). It reads the paper database and nothing else — no broker, no network, no
decisions — so nothing here can touch the loop-decision guardrail.

**Bound to 127.0.0.1 deliberately.** These pages show P&L, strikes, and the full decision journal with
no authentication. The orchestrator reaches it by iframe on the same host.

Three views:
  Today        the session timeline, the payoff curve (the profit forest itself), open positions with
               their floors, the decision journal (as a Gantt strip over its table), and data quality.
  History      filterable trade log, per-arm comparison, a Monday-anchored daily calendar, entry
               windows, fee drag.
  Performance  P&L over daily/weekly/monthly, completion rate and latency, arm divergence.

Every number comes from `analytics.py`, so no figure here can disagree with the EOD report or the
suite card.

Two charts, and they answer different questions. The payoff curve is priced at expiry, so nothing in
it moves during a session; the session timeline puts the same day on a TIME axis, which is the axis
the completion-latency and arm-divergence findings actually live on. Both refuse to smooth over what
they do not know — the timeline breaks its lines across a gap in the record rather than interpolating
a plausible shape through it, and the payoff curve draws one line per arm rather than a blended book,
because the arms are separate books and a combined total would state the book-level claim across all
three (honesty rule 3).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import analytics  # noqa: E402
import db as dbmod  # noqa: E402

# 8801 is MEIC's embed and 8802 is the gex dashboard's, so flies takes the next one. Keep this in
# step with the `dashboard.embeds` entry in the orchestrator's config.example.json.
DEFAULT_PORT = 8803
HOST = "127.0.0.1"


# --------------------------------------------------------------------------- pure helpers
def resolve_port(port_arg: int | None) -> int:
    """Explicit flag wins, then FLIES_DASHBOARD_PORT, then the default. Pure, so it is unit-tested."""
    if port_arg:
        return port_arg
    env = os.environ.get("FLIES_DASHBOARD_PORT")
    if env and env.isdigit():
        return int(env)
    return DEFAULT_PORT


def port_in_use(port: int, host: str = HOST) -> bool:
    """Probe before binding, so a second launch focuses the existing tab instead of dying on EADDRINUSE.
    The orchestrator's embed `ensure_server` relaunches freely, and this is what makes that safe."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def build_api_data(conn, day: str | None = None, arm: str | None = None) -> dict:
    """Everything all three views need, in one payload — the client filters locally from here."""
    day = day or analytics.today()
    arm_filter = None if not arm or arm == "ALL" else arm
    overview = analytics.session_overview(conn, day)

    arms = sorted({b["arm"] for b in overview["books"]} |
                  {r["arm"] for r in analytics.by_arm(conn) if r["arm"]})
    curves = {a: analytics.payoff_curve(conn, day, a) for a in arms} or {}

    return {
        "ok": True,
        "generated_at": analytics.datetime.now().isoformat(timespec="seconds"),
        "date": day,
        "arms": arms,
        "selected_arm": arm or "ALL",
        "today": {
            "stats": overview["stats"],
            "books": overview["books"],
            "positions": overview["positions"],
            "open_count": overview["open_count"],
            "fly_count": overview["fly_count"],
            "risk_free_count": overview["risk_free_count"],
            "completion": overview["completion"],
            "divergence": overview["divergence"],
            "journal": overview["journal"],
            "curves": curves,
            "timeline": analytics.session_timeline(conn, day),
        },
        "history": {
            "trades": analytics.trade_log(conn, arm=arm_filter),
            "by_arm": analytics.by_arm(conn),
            "by_entry_mode": analytics.by_entry_mode(conn),
            "by_window": analytics.by_entry_window(conn),
            "fee_drag": analytics.fee_drag(conn),
            "daily": analytics.daily_pnl(conn, arm=arm_filter),
        },
        "performance": {
            "daily": analytics.pnl_series(conn, "daily", arm=arm_filter),
            "weekly": analytics.pnl_series(conn, "weekly", arm=arm_filter),
            "monthly": analytics.pnl_series(conn, "monthly", arm=arm_filter),
            "all_time": analytics.stats_for_period(conn, arm=arm_filter),
            "completion": analytics.completion_stats(conn),
            "divergence": analytics.arm_divergence(conn),
        },
    }


# --------------------------------------------------------------------------- page
_STYLE = """
:root{--bg:#0f1216;--panel:#161b22;--line:#252c36;--fg:#e6edf3;--dim:#8b949e;
--pos:#3fb950;--neg:#f85149;--accent:#58a6ff;--warn:#d29922}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 ui-sans-serif,system-ui,-apple-system,
"Segoe UI",Roboto,sans-serif}
header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:16px;
flex-wrap:wrap;position:sticky;top:0;background:var(--bg);z-index:10}
h1{font-size:16px;margin:0;font-weight:600}
.badge{font-size:11px;padding:2px 8px;border-radius:10px;background:#1f2937;color:var(--dim)}
nav{display:flex;gap:4px;margin-left:auto;flex-wrap:wrap}
nav button{background:transparent;border:1px solid var(--line);color:var(--dim);padding:5px 12px;
border-radius:6px;cursor:pointer;font-size:13px}
nav button.active{background:var(--panel);color:var(--fg);border-color:var(--accent)}
select,input{background:var(--panel);border:1px solid var(--line);color:var(--fg);padding:4px 8px;
border-radius:6px;font-size:13px}
main{padding:18px 20px 60px}
.view{display:none}.view.active{display:block}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
.card h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);margin:0 0 10px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:14px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.tile .k{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.05em}
.tile .v{font-size:20px;font-weight:600;margin-top:2px}
.pos{color:var(--pos)}.neg{color:var(--neg)}.dim{color:var(--dim)}.warn{color:var(--warn)}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--dim);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.04em}
tbody tr:hover{background:#1c222b}
.num{text-align:right;font-variant-numeric:tabular-nums}
.scroll{overflow-x:auto;max-height:420px;overflow-y:auto}
.empty{color:var(--dim);font-style:italic;padding:14px 4px}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;align-items:center}
.reorder-handle{cursor:grab;color:var(--dim);float:right;user-select:none;font-size:15px;line-height:1}
canvas{width:100%!important}
.cal{display:flex;gap:8px;overflow-x:auto;padding-bottom:4px;align-items:flex-start}
.cal-side{display:grid;grid-template-rows:14px repeat(5,16px);row-gap:3px;font-size:10px;
color:var(--dim);text-align:right;padding-right:2px;flex:none}
.cal-main{display:flex;flex-direction:column}
.cal-months{display:flex;gap:3px;height:14px;margin-bottom:3px;font-size:10px;color:var(--dim)}
.cal-mon{width:16px;white-space:nowrap;overflow:visible;flex:none}
.cal-weeks{display:flex;gap:3px}
.cal-week{display:grid;grid-template-rows:repeat(5,16px);row-gap:3px}
.hcell{width:16px;height:16px;border-radius:3px;background:#21262d}
.note{color:var(--dim);font-size:11.5px;margin-top:10px;line-height:1.5}
.pill{font-size:10.5px;padding:1px 7px;border-radius:9px;border:1px solid var(--line);color:var(--dim)}
.pill.ok{color:var(--pos);border-color:#1f6f33}
.pill.bad{color:var(--neg);border-color:#6f2420}
.legend{display:flex;flex-wrap:wrap;gap:12px;margin-top:8px;font-size:11.5px;color:var(--dim)}
.legend span{display:flex;align-items:center;gap:5px}
.legend i{width:11px;height:3px;border-radius:2px;display:inline-block}
.legend i.dash{height:0;border-top:2px dashed currentColor;background:none!important}
canvas.hoverable{cursor:crosshair}
footer{padding:12px 20px;color:var(--dim);font-size:11.5px;border-top:1px solid var(--line)}
"""

_BODY = """
<header>
  <h1>Flies</h1>
  <span class="badge">paper</span>
  <span class="badge" id="asof">–</span>
  <label class="dim" style="font-size:12px">arm
    <select id="arm-select"><option value="ALL">all</option></select>
  </label>
  <nav>
    <button data-view="today" class="active">Today</button>
    <button data-view="history">History</button>
    <button data-view="performance">Performance</button>
  </nav>
</header>
<main>
  <section class="view active" id="view-today">
    <div class="tiles" id="today-tiles"></div>
    <div class="grid">
      <div class="card" style="grid-column:1/-1"><h2>Session timeline — how the day actually went</h2>
        <canvas id="timeline" height="360"></canvas>
        <div class="legend" id="timeline-legend"></div>
        <div class="note">Spot and each arm's wanted centre on every iteration, entries and
        completions on the same axis, and each leg-in drawn as a bar running to its completion — so
        completion latency reads as a length beside the drift that bought it. The lower panel replays
        the book at each tick: what it would have been worth had the session ended at that moment and
        that price. That is an expiry payoff evaluated at a live spot, <em>not</em> a mark — these
        positions are not quoted intraday.</div>
        <div class="note" id="timeline-feed"></div></div>
      <div class="card" style="grid-column:1/-1"><h2>Payoff at expiry — the profit forest</h2>
        <canvas id="payoff" height="260"></canvas>
        <div class="legend" id="payoff-legend"></div>
        <div class="note" id="payoff-note"></div></div>
      <div class="card"><h2>Positions</h2><div class="scroll"><table id="pos-tbl"></table></div></div>
      <div class="card"><h2>Book floors</h2><div class="scroll"><table id="book-tbl"></table></div></div>
      <div class="card" style="grid-column:1/-1"><h2>Decision journal — why we did or didn't trade</h2>
        <canvas id="journal-gantt" height="120"></canvas>
        <div class="legend" id="journal-legend"></div>
        <div class="scroll"><table id="journal-tbl"></table></div>
        <div class="note">Repeated refusals are collapsed into one counted run, so a quiet day reads as
        a few rows that explain themselves rather than hundreds of identical ones. The strip draws each
        run as a bar over the span it held — a gate that blocked all morning is a bar covering the
        morning, next to the brief green marks where an entry actually fired.</div></div>
      <div class="card"><h2>Arm divergence</h2><div class="scroll"><table id="div-tbl"></table></div>
        <div class="note" id="div-note"></div></div>
    </div>
  </section>

  <section class="view" id="view-history">
    <div class="grid">
      <div class="card"><h2>By arm</h2><div class="scroll"><table id="arm-tbl"></table></div></div>
      <div class="card"><h2>By entry mode</h2><div class="scroll"><table id="mode-tbl"></table></div></div>
      <div class="card"><h2>By entry window</h2><div class="scroll"><table id="win-tbl"></table></div>
        <div class="note">Windows are unranked by design — the ranking is meant to emerge here.</div></div>
      <div class="card"><h2>Fee drag</h2><div class="scroll"><table id="fee-tbl"></table></div>
        <div class="note">A legged fly pays two fee stacks against a credit that may be $35–105.
        Costs are not a rounding error for this strategy.</div></div>
      <div class="card" style="grid-column:1/-1"><h2>Daily P&amp;L</h2><div class="cal" id="heat"></div>
        <div class="note">A settled trading day per cell, Monday at the top of each week column. An
        empty cell is a session that never settled, not a flat one — the two are different findings.</div></div>
      <div class="card" style="grid-column:1/-1"><h2>Trade log</h2>
        <div class="filters">
          <input type="date" id="f-from"><input type="date" id="f-to">
          <select id="f-mode"><option value="">all modes</option><option>legged</option>
            <option>outright</option></select>
          <select id="f-outcome"><option value="">all outcomes</option><option>win</option>
            <option>loss</option><option>pinned</option><option>risk-free</option></select>
          <input id="f-search" placeholder="search…">
          <span class="dim" id="f-count"></span>
        </div>
        <div class="scroll"><table id="log-tbl"></table></div></div>
    </div>
  </section>

  <section class="view" id="view-performance">
    <div class="tiles" id="perf-tiles"></div>
    <div class="grid">
      <div class="card" style="grid-column:1/-1"><h2>P&amp;L over time</h2>
        <div class="filters">
          <select id="perf-gran"><option>daily</option><option>weekly</option><option>monthly</option>
          </select>
          <label class="dim" style="font-size:12px">
            <input type="checkbox" id="perf-cum"> cumulative</label>
        </div>
        <canvas id="perf-chart" height="130"></canvas></div>
      <div class="card"><h2>Completion</h2><div class="scroll"><table id="comp-tbl"></table></div>
        <div class="note">Completion rate is the whole thesis. When a legged entry never completes you
        are holding an ordinary short vertical with full defined risk.</div></div>
      <div class="card"><h2>Why misses missed</h2><div class="scroll"><table id="cf-tbl"></table></div>
        <div class="note">"Never offered" and "buffer too tight" look identical in the P&amp;L and call
        for opposite fixes.</div></div>
    </div>
  </section>
</main>
<footer>
  Read-only view of the paper database · loopback only · paper trades, not advice
  <button id="reset-layout" style="float:right;background:none;border:1px solid var(--line);
  color:var(--dim);border-radius:6px;padding:2px 8px;cursor:pointer">reset layout</button>
</footer>
"""

# Charts are drawn with plain canvas 2D rather than Chart.js. A loopback page that reached out to a
# CDN would break on an offline box and add a third-party dependency to a surface whose entire job is
# to read a local SQLite file. Two small chart functions are a fair trade for that.
_JS = r"""
const $ = s => document.querySelector(s);
const fmtMoney = v => v === null || v === undefined ? '–'
  : (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString(undefined,{maximumFractionDigits:2});
const fmtPct = v => v === null || v === undefined ? '–' : (v*100).toFixed(0) + '%';
const fmtNum = (v,d=2) => v === null || v === undefined ? '–' : Number(v).toFixed(d);
const tone = v => v === null || v === undefined ? '' : (v >= 0 ? 'pos' : 'neg');
let DATA = null, ARM = 'ALL';

function table(el, cols, rows, empty) {
  if (!rows || !rows.length) { el.innerHTML = ''; el.insertAdjacentHTML('afterend', '');
    el.innerHTML = `<tbody><tr><td class="empty">${empty || 'Nothing yet.'}</td></tr></tbody>`; return; }
  const head = '<thead><tr>' + cols.map(c => `<th class="${c.num?'num':''}">${c.h}</th>`).join('') +
    '</tr></thead>';
  const body = '<tbody>' + rows.map(r => '<tr>' + cols.map(c => {
    const v = c.f(r);
    return `<td class="${c.num?'num':''} ${c.tone?c.tone(r):''}">${v === null || v === undefined ? '–' : v}</td>`;
  }).join('') + '</tr>').join('') + '</tbody>';
  el.innerHTML = head + body;
}

function tiles(el, items) {
  el.innerHTML = items.map(i =>
    `<div class="tile"><div class="k">${i.k}</div><div class="v ${i.t||''}">${i.v}</div></div>`).join('');
}

/* ---------- charts (plain canvas, no library) ---------- */
function prep(cv) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.height;
  cv.width = w * dpr; cv.style.height = h + 'px';
  const g = cv.getContext('2d'); g.setTransform(dpr,0,0,dpr,0,0); g.clearRect(0,0,w,h);
  return {g, w, h};
}

/* One colour per arm, assigned by position in the sorted arm list so a given arm keeps its colour
   across both charts and the legend. */
const ARM_COLORS = ['#58a6ff','#d29922','#a371f7','#3fb950','#f778ba'];
const armColor = (arm, arms) => ARM_COLORS[Math.max(0, (arms||[]).indexOf(arm)) % ARM_COLORS.length];
const SPOT_COLOR = '#e6edf3';

/* Round tick values, so the axes carry a readable scale rather than just their two endpoints. */
function ticksFor(min, max, count) {
  const span = (max - min) || 1;
  const raw = span / Math.max(1, count);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1,2,2.5,5,10].map(m => m*mag).find(s => s >= raw) || 10*mag;
  const out = [];
  for (let v = Math.ceil(min/step)*step; v <= max + 1e-9; v += step) out.push(v);
  return out;
}

const minuteOf = ts => {
  const hm = String(ts||'').slice(11,16);
  return (+hm.slice(0,2)) * 60 + (+hm.slice(3,5));
};
const hhmm = m => String(Math.floor(m/60)).padStart(2,'0') + ':' + String(Math.round(m%60)).padStart(2,'0');

/* Hover state is per canvas, keyed by element id, so a crosshair on one chart never redraws the
   other. Each chart registers a redraw thunk and gets a crosshair plus a readout for free. */
const HOVER = {};
function bindHover(cv, redraw) {
  if (cv.dataset.hoverBound) return;
  cv.dataset.hoverBound = '1'; cv.classList.add('hoverable');
  cv.addEventListener('mousemove', e => {
    const b = cv.getBoundingClientRect();
    HOVER[cv.id] = {x: e.clientX - b.left, y: e.clientY - b.top}; redraw();
  });
  cv.addEventListener('mouseleave', () => { delete HOVER[cv.id]; redraw(); });
}

/* A readout box that stays inside the canvas rather than running off the right edge. */
function readout(g, w, h, x, lines) {
  const pad = 6, lh = 13;
  g.font = '11px system-ui';
  const bw = Math.max(...lines.map(l => g.measureText(l).width)) + pad*2;
  const bh = lines.length*lh + pad*2 - 3;
  const bx = Math.min(Math.max(x + 12, 4), w - bw - 4), by = 8;
  g.fillStyle = 'rgba(13,17,23,.92)'; g.strokeStyle = '#252c36';
  g.beginPath(); g.roundRect(bx, by, bw, bh, 6); g.fill(); g.stroke();
  g.fillStyle = '#e6edf3';
  lines.forEach((l,i) => g.fillText(l, bx + pad, by + pad + lh*(i+1) - 4));
}

function legend(el, items) {
  el.innerHTML = items.map(i =>
    `<span style="color:${i.c}"><i class="${i.dash?'dash':''}" style="background:${i.c}"></i>` +
    `<span style="color:var(--dim)">${i.t}</span></span>`).join('');
}

/* The payoff curve: filled green above zero, red below. This is the view the strategy is named for —
   the shape tells you instantly whether a book is genuinely safe or merely safe-looking.

   With "all" selected this draws one line per arm rather than a blended book. The arms are separate
   books by design and a combined total would hide the only contrast the experiment draws — and the
   previous behaviour, silently plotting arms[0] unlabelled whenever the filter said "all", read as
   though it were the whole book. */
function drawPayoff(cv, curves, arms, selected, spot) {
  const {g, w, h} = prep(cv);
  const shown = (selected === 'ALL' ? arms : [selected])
    .filter(a => curves[a] && !curves[a].empty && curves[a].prices.length);
  if (!shown.length) {
    g.fillStyle = '#8b949e'; g.font = '13px system-ui';
    g.fillText('No positions yet today.', 12, h/2); return;
  }
  const pad = {l: 62, r: 12, t: 12, b: 26};
  let xMin = Infinity, xMax = -Infinity, yMin = 0, yMax = 0;
  shown.forEach(a => {
    const c = curves[a];
    xMin = Math.min(xMin, ...c.prices); xMax = Math.max(xMax, ...c.prices);
    yMin = Math.min(yMin, ...c.pnl);    yMax = Math.max(yMax, ...c.pnl);
  });
  const span = (yMax - yMin) || 1; yMin -= span*0.1; yMax += span*0.1;
  const X = v => pad.l + (v - xMin) / ((xMax - xMin)||1) * (w - pad.l - pad.r);
  const Y = v => h - pad.b - (v - yMin) / ((yMax - yMin)||1) * (h - pad.t - pad.b);
  const zero = Y(0);

  g.font = '10px system-ui';
  ticksFor(yMin, yMax, 5).forEach(v => {
    g.strokeStyle = Math.abs(v) < 1e-9 ? '#3d4653' : '#1c222b';
    g.beginPath(); g.moveTo(pad.l, Y(v)); g.lineTo(w - pad.r, Y(v)); g.stroke();
    g.fillStyle = '#8b949e'; g.fillText(fmtMoney(v), 4, Y(v) + 3);
  });
  ticksFor(xMin, xMax, 6).forEach(v => {
    g.strokeStyle = '#1c222b';
    g.beginPath(); g.moveTo(X(v), pad.t); g.lineTo(X(v), h - pad.b); g.stroke();
    g.fillStyle = '#8b949e'; g.fillText(fmtNum(v,0), X(v) - 14, h - 8);
  });

  // A single arm gets the green/red fill — that fill IS the claim "this book cannot lose here", and
  // it is only meaningful for one book at a time.
  if (shown.length === 1) {
    const c = curves[shown[0]], xs = c.prices, ys = c.pnl;
    [[1,'rgba(63,185,80,.28)'],[-1,'rgba(248,81,73,.24)']].forEach(([sign, fill]) => {
      g.beginPath(); g.moveTo(X(xs[0]), zero);
      xs.forEach((x,i) => g.lineTo(X(x), Y(sign > 0 ? Math.max(ys[i],0) : Math.min(ys[i],0))));
      g.lineTo(X(xs[xs.length-1]), zero); g.closePath();
      g.fillStyle = fill; g.fill();
    });
  }

  shown.forEach(a => {
    const c = curves[a], col = armColor(a, arms);
    g.beginPath();
    c.prices.forEach((x,i) => i ? g.lineTo(X(x), Y(c.pnl[i])) : g.moveTo(X(x), Y(c.pnl[i])));
    g.strokeStyle = col; g.lineWidth = shown.length === 1 ? 1.8 : 1.4; g.stroke();
    (c.centers||[]).forEach(k => {
      g.strokeStyle = col; g.globalAlpha = .35; g.setLineDash([3,3]);
      g.beginPath(); g.moveTo(X(k), pad.t); g.lineTo(X(k), h - pad.b); g.stroke();
      g.setLineDash([]); g.globalAlpha = 1;
    });
  });

  if (spot) {
    g.strokeStyle = SPOT_COLOR; g.lineWidth = 1; g.globalAlpha = .7;
    g.beginPath(); g.moveTo(X(spot), pad.t); g.lineTo(X(spot), h - pad.b); g.stroke();
    g.globalAlpha = 1;
  }

  const hv = HOVER[cv.id];
  if (hv && hv.x >= pad.l && hv.x <= w - pad.r) {
    const price = xMin + (hv.x - pad.l) / (w - pad.l - pad.r) * (xMax - xMin);
    g.strokeStyle = '#3d4653'; g.beginPath();
    g.moveTo(hv.x, pad.t); g.lineTo(hv.x, h - pad.b); g.stroke();
    readout(g, w, h, hv.x, [`at ${fmtNum(price,0)}`, ...shown.map(a => {
      const c = curves[a];
      let best = 0;
      c.prices.forEach((p,i) => { if (Math.abs(p-price) < Math.abs(c.prices[best]-price)) best = i; });
      return `${a}  ${fmtMoney(c.pnl[best])}`;
    })]);
  }

  legend($('#payoff-legend'), [
    ...shown.map(a => ({c: armColor(a, arms), t: a})),
    {c: 'rgba(139,148,158,.8)', t: 'centres', dash: 1},
    {c: SPOT_COLOR, t: 'spot now'},
  ]);
}

/* The session timeline: the same day along a TIME axis.

   Top panel is price — spot, each arm's wanted centre, and every leg-in drawn as a bar running to
   its completion, so latency is a length you can read against the drift beside it. Bottom panel is
   the book replayed at each tick. See `analytics.session_timeline` for why that lower track is an
   expiry payoff at a live spot and not a mark. */
function drawTimeline(cv, tl, selected) {
  const {g, w, h} = prep(cv);
  const arms = tl && tl.arms || [];
  const shown = selected === 'ALL' ? arms : arms.filter(a => a === selected);
  const ticks = (tl && tl.ticks || []).filter(t => t.spot !== null && t.spot !== undefined);
  if (!ticks.length) {
    g.fillStyle = '#8b949e'; g.font = '13px system-ui';
    g.fillText('No iterations recorded yet today.', 12, h/2);
    legend($('#timeline-legend'), []); return;
  }
  const pad = {l: 62, r: 12, t: 12, b: 24};
  const splitGap = 26;
  const priceBot = pad.t + (h - pad.t - pad.b - splitGap) * 0.66;
  const pnlTop = priceBot + splitGap;

  const mins = ticks.map(t => minuteOf(t.ts));
  const tMin = Math.min(...mins), tMax = Math.max(...mins);
  const X = m => pad.l + (m - tMin) / ((tMax - tMin)||1) * (w - pad.l - pad.r);

  // Where the loop went quiet, BREAK the lines rather than joining across the hole.
  //
  // A straight interpolated segment over a two-hour silence looks like a calm market and reads as
  // evidence; it is the absence of evidence. This module refuses to guess elsewhere for the same
  // reason — the provider returns a refusal rather than a stale quote — so the chart should not
  // quietly invent the shape of a gap either.
  const steps = mins.slice(1).map((m,i) => m - mins[i]).filter(d => d > 0).sort((a,b) => a-b);
  const median = steps.length ? steps[Math.floor(steps.length/2)] : 0;
  const gapLimit = Math.max(median * 3, 5);
  const isGap = i => i > 0 && (mins[i] - mins[i-1]) > gapLimit;

  // What the feed did during a gap tells the two silences apart: refused snapshots (feed stale/down)
  // vs no rows at all (the loop itself was not running). The first is a data problem, the second is
  // an ops problem, and they were indistinguishable before fly_snapshots recorded the refusals.
  const feedMins = (tl.feed || []).map(f => ({m: minuteOf(f.ts), status: f.status}));
  const gapReason = (a, b) => {
    const refused = feedMins.filter(f => f.m > a && f.m < b && f.status !== 'ok');
    if (!refused.length) return 'loop silent';
    const counts = {};
    refused.forEach(f => { counts[f.status] = (counts[f.status] || 0) + 1; });
    const top = Object.entries(counts).sort((x,y) => y[1]-x[1])[0];
    return `${top[0]} ×${top[1]}`;
  };

  // --- price panel scale: spot plus every centre any shown arm asked for
  let pMin = Infinity, pMax = -Infinity;
  ticks.forEach(t => {
    pMin = Math.min(pMin, t.spot); pMax = Math.max(pMax, t.spot);
    shown.forEach(a => { const c = t.centers[a];
      if (c != null) { pMin = Math.min(pMin, c); pMax = Math.max(pMax, c); } });
  });
  // Structures we actually hold must fit on the axis even if no iteration ever wanted that strike —
  // an entry drawn off-canvas is worse than no entry marker at all.
  (tl.events || []).concat(tl.waiting || []).filter(e => shown.includes(e.arm)).forEach(e => {
    if (e.center != null) { pMin = Math.min(pMin, e.center); pMax = Math.max(pMax, e.center); }
  });
  const pSpan = (pMax - pMin) || 1; pMin -= pSpan*0.12; pMax += pSpan*0.12;
  const PY = v => priceBot - (v - pMin) / ((pMax - pMin)||1) * (priceBot - pad.t);

  // --- pnl panel scale
  let vMin = 0, vMax = 0;
  ticks.forEach(t => shown.forEach(a => { const v = t.settle_now[a];
    if (v != null) { vMin = Math.min(vMin, v); vMax = Math.max(vMax, v); } }));
  const vSpan = (vMax - vMin) || 1; vMin -= vSpan*0.15; vMax += vSpan*0.15;
  const VY = v => h - pad.b - (v - vMin) / ((vMax - vMin)||1) * (h - pad.b - pnlTop);

  g.font = '10px system-ui';
  ticksFor(pMin, pMax, 4).forEach(v => {
    g.strokeStyle = '#1c222b'; g.beginPath();
    g.moveTo(pad.l, PY(v)); g.lineTo(w - pad.r, PY(v)); g.stroke();
    g.fillStyle = '#8b949e'; g.fillText(fmtNum(v,0), 4, PY(v) + 3);
  });
  ticksFor(vMin, vMax, 3).forEach(v => {
    g.strokeStyle = Math.abs(v) < 1e-9 ? '#3d4653' : '#1c222b';
    g.beginPath(); g.moveTo(pad.l, VY(v)); g.lineTo(w - pad.r, VY(v)); g.stroke();
    g.fillStyle = '#8b949e'; g.fillText(fmtMoney(v), 4, VY(v) + 3);
  });
  ticksFor(tMin, tMax, 7).forEach(m => {
    g.strokeStyle = '#1c222b'; g.beginPath();
    g.moveTo(X(m), pad.t); g.lineTo(X(m), h - pad.b); g.stroke();
    g.fillStyle = '#8b949e'; g.fillText(hhmm(m), X(m) - 13, h - 7);
  });
  g.fillStyle = '#8b949e';
  g.fillText('settled if the day ended here', pad.l + 4, pnlTop - 7);

  // Mark the silences, so a hole in the record is something the page states rather than hides.
  ticks.forEach((t,i) => {
    if (!isGap(i)) return;
    const x0 = X(mins[i-1]), x1 = X(mins[i]);
    g.fillStyle = 'rgba(210,153,34,.07)'; g.fillRect(x0, pad.t, x1-x0, h - pad.b - pad.t);
    g.fillStyle = 'rgba(210,153,34,.8)'; g.font = '10px system-ui';
    // Two short lines rather than one long one — a 40-minute band is narrower than the full label,
    // and stacking is what keeps the reason legible inside the gap it explains.
    [`no data · ${Math.round(mins[i]-mins[i-1])}m`, gapReason(mins[i-1], mins[i])].forEach((s, k) => {
      if (x1 - x0 > g.measureText(s).width + 6)
        g.fillText(s, (x0+x1)/2 - g.measureText(s).width/2, pad.t + 11 + k*12);
    });
  });

  // Each arm's wanted centre — a step line, since a centre holds until the arm picks another.
  //
  // Solid and half-faded rather than dashed. A dashed step over ~150 iterations of a strike that
  // moves in 5-point jumps renders as a field of boxes that buries the spot line underneath it; the
  // divergence between arms is the signal here, and it only reads once the texture is gone.
  shown.forEach(a => {
    g.strokeStyle = armColor(a, arms); g.lineWidth = 1.3; g.globalAlpha = .6;
    g.beginPath();
    let started = false, prevY = null;
    ticks.forEach((t,i) => {
      const c = t.centers[a]; if (c == null) return;
      const x = X(mins[i]), y = PY(c);
      if (!started || isGap(i)) { g.moveTo(x, y); started = true; }
      else { g.lineTo(x, prevY); g.lineTo(x, y); }
      prevY = y;
    });
    g.stroke(); g.globalAlpha = 1;
  });

  // Spot goes on top of the centres, not under them: it is the reference every other mark is read
  // against.
  g.beginPath();
  ticks.forEach((t,i) => (i && !isGap(i)) ? g.lineTo(X(mins[i]), PY(t.spot))
                                          : g.moveTo(X(mins[i]), PY(t.spot)));
  g.strokeStyle = SPOT_COLOR; g.lineWidth = 1.8; g.stroke();

  // leg-in -> completion spans, drawn at the fly's centre
  (tl.spans || []).filter(s => shown.includes(s.arm)).forEach(s => {
    const x0 = X(minuteOf(s.from)), x1 = X(minuteOf(s.to)), y = PY(s.center);
    g.strokeStyle = armColor(s.arm, arms); g.lineWidth = 5; g.globalAlpha = .3;
    g.beginPath(); g.moveTo(x0, y); g.lineTo(Math.max(x1, x0 + 2), y); g.stroke();
    g.globalAlpha = 1; g.lineWidth = 1;
  });

  // Spreads still waiting run to the right edge, dashed and open-ended. This is the branch carrying
  // full defined risk, and on a time axis it is visible while it is still happening rather than only
  // once settlement resolves it.
  (tl.waiting || []).filter(s => shown.includes(s.arm)).forEach(s => {
    const y = PY(s.center);
    g.strokeStyle = armColor(s.arm, arms); g.lineWidth = 5; g.globalAlpha = .3;
    g.setLineDash([7,5]);
    g.beginPath(); g.moveTo(X(minuteOf(s.from)), y); g.lineTo(w - pad.r, y); g.stroke();
    g.setLineDash([]); g.globalAlpha = 1; g.lineWidth = 1;
  });

  // events: entry = hollow ring at spot, completion = filled diamond at the centre
  (tl.events || []).filter(e => shown.includes(e.arm)).forEach(e => {
    const col = armColor(e.arm, arms);
    const x = X(minuteOf(e.ts));
    if (e.kind === 'entry') {
      const y = PY(e.spot != null ? e.spot : e.center);
      g.strokeStyle = col; g.lineWidth = 1.6;
      g.beginPath(); g.arc(x, y, 4, 0, Math.PI*2); g.stroke();
    } else {
      const y = PY(e.center);
      g.fillStyle = col;
      g.beginPath(); g.moveTo(x, y-5); g.lineTo(x+5, y); g.lineTo(x, y+5); g.lineTo(x-5, y);
      g.closePath(); g.fill();
    }
  });

  // the replayed book
  shown.forEach(a => {
    const pts = ticks.map((t,i) => ({m: mins[i], v: t.settle_now[a], gap: isGap(i)}))
                     .filter(p => p.v != null);
    if (!pts.length) return;
    if (shown.length === 1) {
      g.beginPath(); g.moveTo(X(pts[0].m), VY(0));
      pts.forEach(p => g.lineTo(X(p.m), VY(p.v)));
      g.lineTo(X(pts[pts.length-1].m), VY(0)); g.closePath();
      g.fillStyle = pts[pts.length-1].v >= 0 ? 'rgba(63,185,80,.22)' : 'rgba(248,81,73,.2)';
      g.fill();
    }
    g.beginPath();
    pts.forEach((p,i) => (i && !p.gap) ? g.lineTo(X(p.m), VY(p.v)) : g.moveTo(X(p.m), VY(p.v)));
    g.strokeStyle = armColor(a, arms); g.lineWidth = 1.5; g.stroke();
  });

  const hv = HOVER[cv.id];
  if (hv && hv.x >= pad.l && hv.x <= w - pad.r) {
    let i = 0;
    mins.forEach((m,j) => { if (Math.abs(X(m)-hv.x) < Math.abs(X(mins[i])-hv.x)) i = j; });
    const t = ticks[i];
    g.strokeStyle = '#3d4653'; g.beginPath();
    g.moveTo(X(mins[i]), pad.t); g.lineTo(X(mins[i]), h - pad.b); g.stroke();
    readout(g, w, h, hv.x, [
      `${hhmm(mins[i])}   spot ${fmtNum(t.spot,2)}`,
      ...shown.map(a => `${a}  centre ${t.centers[a] != null ? fmtNum(t.centers[a],0) : '–'}` +
        `  ${t.settle_now[a] != null ? fmtMoney(t.settle_now[a]) : '–'}`),
    ]);
  }

  legend($('#timeline-legend'), [
    {c: SPOT_COLOR, t: 'spot'},
    ...shown.map(a => ({c: armColor(a, arms), t: `${a} — wanted centre`})),
    {c: '#8b949e', t: '○ credit spread sold   ◆ completed into a fly   ' +
                      '▬ solid bar = time to complete, dashed = still waiting'},
  ]);
}

function drawBars(cv, labels, values) {
  const {g, w, h} = prep(cv);
  if (!values.length) { g.fillStyle='#8b949e'; g.font='13px system-ui';
    g.fillText('Not enough history yet.', 12, h/2); return; }
  const pad = {l:58, r:12, t:12, b:24};
  let yMin = Math.min(...values, 0), yMax = Math.max(...values, 0);
  const span = (yMax-yMin)||1; yMin -= span*0.08; yMax += span*0.08;
  const Y = v => h - pad.b - (v - yMin)/((yMax-yMin)||1)*(h-pad.t-pad.b);
  const bw = (w - pad.l - pad.r) / values.length;
  g.strokeStyle='#252c36'; g.beginPath(); g.moveTo(pad.l, Y(0)); g.lineTo(w-pad.r, Y(0)); g.stroke();
  g.fillStyle='#8b949e'; g.font='10px system-ui';
  [yMax,0,yMin].forEach(v => g.fillText(fmtMoney(v), 4, Y(v)+3));
  values.forEach((v,i) => {
    g.fillStyle = v >= 0 ? '#3fb950' : '#f85149';
    const y0 = Y(0), y1 = Y(v);
    g.fillRect(pad.l + i*bw + bw*0.15, Math.min(y0,y1), Math.max(bw*0.7,1), Math.abs(y1-y0) || 1);
  });
  g.fillStyle='#8b949e';
  if (labels.length) {
    g.fillText(labels[0], pad.l, h-6);
    g.fillText(labels[labels.length-1], w - pad.r - 56, h-6);
  }
}

/* The decision journal as a Gantt strip. The journal already stores each run's first_seen/last_seen,
   so a refusal that held all morning IS an interval — drawing it as one bar says at a glance what an
   eight-column table of HH:MM strings makes you reconstruct. One lane per (arm, mode); an accepted
   run is a brief green mark where a trade actually fired, a refusal is a translucent red span. */
function drawJournalGantt(cv, journal) {
  const rows = (journal || []).filter(r => r.first_seen && r.last_seen);
  const lanes = [...new Set(rows.map(r => `${r.arm}|${r.mode}`))].sort();
  cv.height = Math.max(56, lanes.length * 22 + 30);
  const {g, w, h} = prep(cv);
  if (!rows.length) {
    g.fillStyle = '#8b949e'; g.font = '13px system-ui';
    g.fillText('No decisions recorded yet today.', 12, h/2);
    legend($('#journal-legend'), []); return;
  }
  const pad = {l: 132, r: 12, t: 6, b: 18};
  const times = rows.flatMap(r => [minuteOf(r.first_seen), minuteOf(r.last_seen)]);
  let tMin = Math.min(...times), tMax = Math.max(...times);
  if (tMax - tMin < 1) tMax = tMin + 1;
  const X = m => pad.l + (m - tMin) / (tMax - tMin) * (w - pad.l - pad.r);
  const laneH = (h - pad.t - pad.b) / lanes.length;

  g.font = '10px system-ui';
  ticksFor(tMin, tMax, 6).forEach(m => {
    g.strokeStyle = '#1c222b';
    g.beginPath(); g.moveTo(X(m), pad.t); g.lineTo(X(m), h - pad.b); g.stroke();
    g.fillStyle = '#8b949e'; g.fillText(hhmm(m), X(m) - 13, h - 5);
  });

  const bars = [];
  lanes.forEach((lane, li) => {
    const [arm, mode] = lane.split('|');
    const cy = pad.t + li * laneH + laneH/2;
    g.fillStyle = '#8b949e'; g.font = '10px system-ui';
    g.fillText(`${arm} · ${mode}`, 4, cy + 3);
    rows.filter(r => `${r.arm}|${r.mode}` === lane).forEach(r => {
      const x0 = X(minuteOf(r.first_seen));
      const bw = Math.max(X(minuteOf(r.last_seen)) - x0, 3);
      const bh = Math.min(12, laneH - 5);
      g.fillStyle = r.accepted ? '#3fb950' : 'rgba(248,81,73,.5)';
      g.fillRect(x0, cy - bh/2, bw, bh);
      bars.push({x0, x1: x0 + bw, y0: cy - bh/2, y1: cy + bh/2, r});
    });
  });

  const hv = HOVER[cv.id];
  if (hv) {
    const hit = bars.find(b => hv.x >= b.x0-2 && hv.x <= b.x1+2 && hv.y >= b.y0-3 && hv.y <= b.y1+3);
    if (hit) {
      const r = hit.r;
      readout(g, w, h, hv.x, [
        `${r.arm} · ${r.mode}`,
        `${r.accepted ? '✓ ' : ''}${r.reason}`,
        `${r.first_seen.slice(11,16)}–${r.last_seen.slice(11,16)} · ${r.occurrences}× seen`,
      ]);
    }
  }

  legend($('#journal-legend'), [
    {c: '#3fb950', t: 'entry taken'}, {c: 'rgba(248,81,73,.6)', t: 'refused (bar spans how long)'},
  ]);
}

/* The daily P&L as a proper calendar rather than a flat wrap of squares: week columns, weekdays down,
   Monday-anchored to match the weekly buckets in analytics. Trading is Mon-Fri, so the grid is five
   rows and weekends are simply absent. An empty weekday cell is a session that never settled — a
   different thing from a flat day, and the strategy's whole point is not to blur those. */
function renderCalendar(days) {
  const el = $('#heat');
  if (!days || !days.length) { el.innerHTML = '<span class="empty">No settled days yet.</span>'; return; }
  const byDate = new Map(days.map(d => [d.date, d]));
  const max = Math.max(1, ...days.map(x => Math.abs(x.net_pnl || 0)));
  const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  // Parse and step dates in UTC so a local timezone never shifts a session onto the wrong day.
  const parse = s => new Date(s + 'T00:00:00Z');
  const weekday = dt => (dt.getUTCDay() + 6) % 7;   // Monday = 0
  const iso = dt => dt.toISOString().slice(0, 10);
  const addDays = (dt, n) => { const c = new Date(dt); c.setUTCDate(c.getUTCDate() + n); return c; };

  const first = addDays(parse(days[0].date), -weekday(parse(days[0].date)));  // that week's Monday
  const last = parse(days[days.length - 1].date);
  const weeks = [];
  for (let wk = new Date(first); wk <= last; wk = addDays(wk, 7)) weeks.push(new Date(wk));

  const months = weeks.map((wk, i) => {
    const m = wk.getUTCMonth();
    const label = (i === 0 || m !== weeks[i-1].getUTCMonth()) ? MON[m] : '';
    return `<span class="cal-mon">${label}</span>`;
  }).join('');

  const grid = weeks.map(wk => {
    let cells = '';
    for (let r = 0; r < 5; r++) {                     // Mon..Fri
      const dt = addDays(wk, r), key = iso(dt);
      if (dt < first || dt > last) { cells += '<div class="hcell" style="visibility:hidden"></div>'; continue; }
      const d = byDate.get(key);
      if (!d) { cells += `<div class="hcell" title="${key}: no settled session"></div>`; continue; }
      const v = d.net_pnl || 0, a = Math.min(1, Math.abs(v)/max)*0.85 + 0.15;
      const col = v > 0 ? `rgba(63,185,80,${a})` : v < 0 ? `rgba(248,81,73,${a})` : '#30363d';
      cells += `<div class="hcell" style="background:${col}" title="${key}: ${fmtMoney(v)} (${d.trades} trades)"></div>`;
    }
    return `<div class="cal-week">${cells}</div>`;
  }).join('');

  el.innerHTML =
    '<div class="cal-side"><div></div><div>Mon</div><div></div><div>Wed</div><div></div><div>Fri</div></div>' +
    `<div class="cal-main"><div class="cal-months">${months}</div><div class="cal-weeks">${grid}</div></div>`;
}

/* ---------- renderers ---------- */
function renderToday(d) {
  const t = d.today, s = t.stats, c = t.completion;
  tiles($('#today-tiles'), [
    {k:'Net P&L', v:fmtMoney(s.net_pnl), t:tone(s.net_pnl)},
    {k:'Positions', v:t.positions.length},
    {k:'Open', v:t.open_count},
    {k:'Risk-free', v:`${t.risk_free_count}/${t.fly_count}`, t:t.risk_free_count?'pos':''},
    {k:'Completion', v:fmtPct(c.completion_rate)},
    {k:'Fees', v:fmtMoney(s.fees), t:'dim'},
  ]);

  const lastTick = ((t.timeline || {}).ticks || []).filter(x => x.spot != null).slice(-1)[0];
  const spot = lastTick ? lastTick.spot
    : (t.positions.find(p => p.underlying_at_entry) || {}).underlying_at_entry;
  drawPayoff($('#payoff'), t.curves, d.arms, ARM, spot);
  drawTimeline($('#timeline'), t.timeline, ARM);

  // The feed's own report card: how many ticks actually built a snapshot, and what refused the rest.
  // A low build rate reframes a flat day as a thin-data day — the reading CLAUDE.md promises but that
  // only the module log could give before this.
  const fs = (t.timeline || {}).feed_summary;
  $('#timeline-feed').innerHTML = !fs || !fs.ticks ? ''
    : `Feed: ${fs.ok}/${fs.ticks} ticks built a snapshot (${fmtPct(fs.ok_rate)})` +
      (fs.refused ? ' · refused ' + fs.refused + ': ' +
        Object.entries(fs.by_reason).map(([k,v]) => `${k} ×${v}`).join(', ') : ' · no refusals');
  bindHover($('#payoff'), () => drawPayoff($('#payoff'), t.curves, d.arms, ARM, spot));
  bindHover($('#timeline'), () => drawTimeline($('#timeline'), t.timeline, ARM));

  // One floor sentence per arm. A single blended line would state the book-level claim across arms
  // that are deliberately separate books — and rule 3 exists because that claim is the easy lie.
  const armsShown = (ARM === 'ALL' ? d.arms : [ARM]).filter(a => t.curves[a] && !t.curves[a].empty);
  $('#payoff-note').innerHTML = !armsShown.length
    ? 'No positions yet today — the curve appears once an arm has something on.'
    : armsShown.map(a => {
        const f = t.curves[a].floor;
        const body = f.floor_holds
          ? `floor ${fmtMoney(f.worst)}, holds at every price — this book cannot lose.`
          : `worst case ${fmtMoney(f.worst)} at ${fmtNum(f.worst_at,0)}` +
            (f.band ? `, profitable between ${fmtNum(f.band[0],0)} and ${fmtNum(f.band[1],0)}` : '') +
            (f.unbounded_below ? ', and loses outside that band.' : '.');
        return `<span style="color:${armColor(a, d.arms)}">${a}</span> — ${body}`;
      }).join('<br>');

  table($('#pos-tbl'), [
    {h:'Arm', f:r=>r.arm}, {h:'Mode', f:r=>r.entry_mode},
    {h:'Kind', f:r=>r.kind === 'fly' ? 'fly' : `short ${r.side}`},
    {h:'Centre', f:r=>fmtNum(r.center,0), num:1},
    {h:'Net', f:r=>fmtNum(r.net), num:1},
    {h:'Floor', f:r=>fmtMoney(r.floor_dollars), num:1, tone:r=>tone(r.floor_dollars)},
    {h:'', f:r=>r.risk_free ? '<span class="pill ok">risk-free</span>' :
        (r.kind==='fly'?'<span class="pill bad">floor negative</span>':'<span class="pill">at risk</span>')},
    {h:'Status', f:r=>r.status},
  ], t.positions, 'No positions today.');

  table($('#book-tbl'), [
    {h:'Arm', f:r=>r.arm},
    {h:'Credit', f:r=>fmtMoney(r.credit_collected), num:1},
    {h:'Debits', f:r=>fmtMoney(r.debits_paid), num:1},
    {h:'Fees', f:r=>fmtMoney(r.fees), num:1},
    {h:'Worst', f:r=>fmtMoney(r.worst), num:1, tone:r=>tone(r.worst)},
    {h:'Band', f:r=>r.band_low === null ? '–' : `${fmtNum(r.band_low,0)}–${fmtNum(r.band_high,0)}`},
    {h:'', f:r=>r.floor_holds ? '<span class="pill ok">holds</span>'
        : '<span class="pill bad">bounded</span>'},
  ], t.books, 'No books today.');

  drawJournalGantt($('#journal-gantt'), t.journal);
  bindHover($('#journal-gantt'), () => drawJournalGantt($('#journal-gantt'), t.journal));
  table($('#journal-tbl'), [
    {h:'Arm', f:r=>r.arm}, {h:'Mode', f:r=>r.mode},
    {h:'Decision', f:r=>r.accepted ? `<span class="pill ok">${r.reason}</span>` : r.reason},
    {h:'n', f:r=>r.occurrences, num:1},
    {h:'From', f:r=>(r.first_seen||'').slice(11,16)},
    {h:'To', f:r=>(r.last_seen||'').slice(11,16)},
    {h:'Centre', f:r=>r.center_last === null ? '–' : fmtNum(r.center_last,0), num:1},
    {h:'Detail', f:r=>r.detail || ''},
  ], t.journal, 'No decisions recorded yet today.');

  const dv = t.divergence;
  table($('#div-tbl'), [
    {h:'Pair', f:r=>r.arms}, {h:'Iterations', f:r=>r.iterations, num:1},
    {h:'Agreed', f:r=>fmtPct(r.agreement_rate), num:1},
  ], dv.pairs, 'Not enough iterations yet.');
  $('#div-note').textContent = dv.all_agree_rate === null ? ''
    : `All three arms agreed on ${fmtPct(dv.all_agree_rate)} of ${dv.iterations} iterations. ` +
      (dv.all_agree_rate > 0.8
        ? 'High agreement means the arms are hard to tell apart — separating them would need far more sample than it appears.'
        : 'Healthy disagreement: the arms are genuinely testing different choices.');
}

function renderHistory(d) {
  const h = d.history;
  const perf = [
    {h:'Arm', f:r=>r.arm},{h:'Trades', f:r=>r.trades, num:1},
    {h:'Net', f:r=>fmtMoney(r.net_pnl), num:1, tone:r=>tone(r.net_pnl)},
    {h:'Win', f:r=>fmtPct(r.win_rate), num:1},
    {h:'Avg', f:r=>fmtMoney(r.avg_pnl), num:1},
    {h:'PF', f:r=>fmtNum(r.profit_factor), num:1},
  ];
  table($('#arm-tbl'), perf, h.by_arm, 'No settled trades yet.');
  table($('#mode-tbl'), [{h:'Mode', f:r=>r.entry_mode}, ...perf.slice(1)], h.by_entry_mode,
    'No settled trades yet.');
  table($('#win-tbl'), [{h:'Window', f:r=>r.window}, ...perf.slice(1)], h.by_window,
    'No settled trades yet.');
  table($('#fee-tbl'), [
    {h:'Arm', f:r=>r.arm},{h:'Gross', f:r=>fmtMoney(r.gross_pnl), num:1},
    {h:'Fees', f:r=>fmtMoney(r.fees), num:1},
    {h:'Net', f:r=>fmtMoney(r.net_pnl), num:1, tone:r=>tone(r.net_pnl)},
    {h:'Drag', f:r=>r.fee_drag_pct === null ? '–' : r.fee_drag_pct.toFixed(1)+'%', num:1,
     tone:r=>r.fee_drag_pct > 30 ? 'neg' : ''},
  ], h.fee_drag, 'No settled trades yet.');

  renderCalendar(h.daily);

  renderLog();
}

function renderLog() {
  const rows = (DATA.history.trades || []).filter(t => {
    const from = $('#f-from').value, to = $('#f-to').value;
    if (from && t.trade_date < from) return false;
    if (to && t.trade_date > to) return false;
    const mode = $('#f-mode').value;
    if (mode && t.entry_mode !== mode) return false;
    const oc = $('#f-outcome').value;
    if (oc === 'win' && !(t.pnl > 0)) return false;
    if (oc === 'loss' && !(t.pnl < 0)) return false;
    if (oc === 'pinned' && !t.pinned) return false;
    if (oc === 'risk-free' && !t.risk_free) return false;
    const q = $('#f-search').value.trim().toLowerCase();
    if (q && !JSON.stringify(t).toLowerCase().includes(q)) return false;
    return true;
  });
  $('#f-count').textContent = `${rows.length} trade${rows.length===1?'':'s'}`;
  table($('#log-tbl'), [
    {h:'Date', f:r=>r.trade_date},{h:'Arm', f:r=>r.arm},{h:'Mode', f:r=>r.entry_mode},
    {h:'Kind', f:r=>r.kind === 'fly' ? 'fly' : `short ${r.side}`},
    {h:'Centre', f:r=>fmtNum(r.center,0), num:1},
    {h:'Window', f:r=>r.entry_window || '–'},
    {h:'Net', f:r=>fmtNum(r.net), num:1},
    {h:'Fees', f:r=>fmtMoney(r.fees), num:1},
    {h:'P&L', f:r=>fmtMoney(r.pnl), num:1, tone:r=>tone(r.pnl)},
    {h:'Latency', f:r=>r.completion_latency_min === null ? '–' : r.completion_latency_min+'m', num:1},
    {h:'', f:r=>r.pinned ? '<span class="pill ok">pinned</span>' : ''},
  ], rows, 'No trades match these filters.');
}

function renderPerformance(d) {
  const p = d.performance, a = p.all_time, c = p.completion;
  tiles($('#perf-tiles'), [
    {k:'Net P&L', v:fmtMoney(a.net_pnl), t:tone(a.net_pnl)},
    {k:'Trades', v:a.trades},
    {k:'Win rate', v:fmtPct(a.win_rate)},
    {k:'Profit factor', v:fmtNum(a.profit_factor)},
    {k:'Fee drag', v:a.fee_drag_pct === null ? '–' : a.fee_drag_pct.toFixed(1)+'%',
     t:a.fee_drag_pct > 30 ? 'neg' : ''},
    {k:'Completion', v:fmtPct(c.completion_rate)},
  ]);
  const series = p[$('#perf-gran').value] || [];
  const cum = $('#perf-cum').checked;
  drawBars($('#perf-chart'), series.map(b=>b.bucket),
           series.map(b => cum ? b.cumulative_pnl : b.net_pnl));

  table($('#comp-tbl'), [
    {h:'Metric', f:r=>r.k},{h:'Value', f:r=>r.v, num:1},
  ], [
    {k:'Legged entries', v:c.legged_entries},
    {k:'Completed', v:c.completed},
    {k:'Completion rate', v:fmtPct(c.completion_rate)},
    {k:'Median latency', v:c.median_latency_min === null ? '–' : c.median_latency_min+' min'},
    {k:'Latency range', v:c.min_latency_min === null ? '–'
        : `${c.min_latency_min}–${c.max_latency_min} min`},
    {k:'Median spot move', v:fmtNum(c.median_spot_move,1)},
  ]);
  table($('#cf-tbl'), [{h:'Verdict', f:r=>r.k},{h:'Count', f:r=>r.v, num:1}], [
    {k:'Market never offered it', v:c.never_offered},
    {k:'Buffer too tight', v:c.buffer_too_tight},
    {k:'Never priced', v:c.counterfactual_unknown},
  ]);
}

function renderAll(d) {
  DATA = d;
  $('#asof').textContent = `${d.date} · ${d.generated_at.slice(11,16)}`;
  const sel = $('#arm-select');
  if (sel.options.length - 1 !== d.arms.length) {
    sel.innerHTML = '<option value="ALL">all</option>' +
      d.arms.map(a => `<option>${a}</option>`).join('');
    sel.value = ARM;
  }
  renderToday(d); renderHistory(d); renderPerformance(d);
}

async function refresh() {
  try {
    const r = await fetch(`/api/data?arm=${encodeURIComponent(ARM)}`);
    const d = await r.json();
    if (d.ok) renderAll(d);
  } catch (e) { /* transient; the next tick retries */ }
}

document.querySelectorAll('nav button').forEach(b => b.onclick = () => {
  document.querySelectorAll('nav button').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  b.classList.add('active');
  $('#view-' + b.dataset.view).classList.add('active');
  if (DATA) renderAll(DATA);   // canvases size wrongly while hidden
});
$('#arm-select').onchange = e => { ARM = e.target.value; refresh(); };
['#f-from','#f-to','#f-mode','#f-outcome','#f-search'].forEach(s => {
  $(s).oninput = renderLog; $(s).onchange = renderLog;
});
$('#perf-gran').onchange = () => renderPerformance(DATA);
$('#perf-cum').onchange = () => renderPerformance(DATA);
window.addEventListener('resize', () => { if (DATA) renderAll(DATA); });

refresh();
setInterval(refresh, 15000);

/* ---------- drag-to-reorder (same behaviour as the MEIC and orchestrator dashboards) ---------- */
(function(){
  const LS_KEY = 'flies-dash-layout-v1';
  const groups = () => document.querySelectorAll('.grid');
  const store = () => { try { return JSON.parse(localStorage.getItem(LS_KEY)) || {}; }
                        catch(e){ return {}; } };
  const gkey = g => (g.closest('.view')||{}).id || 'root';
  const ckey = (c,i) => (c.querySelector('h2')||{}).textContent
      ? (c.querySelector('h2').textContent.toLowerCase().replace(/[^a-z0-9]+/g,'-')) : 'card-'+i;
  const srcOrder = new Map();

  groups().forEach(g => {
    srcOrder.set(gkey(g), [...g.children]);
    [...g.children].forEach((c,i) => {
      c.dataset.rkey = ckey(c,i);
      const handle = document.createElement('span');
      handle.className = 'reorder-handle'; handle.textContent = '⠿'; handle.draggable = true;
      // The HANDLE is the drag source, not the card: toggling a card's draggable on mousedown is
      // unreliable in Chrome, which is the same reason MEIC's dashboard does it this way.
      handle.addEventListener('dragstart', e => {
        c.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', c.dataset.rkey);
      });
      handle.addEventListener('dragend', () => { c.classList.remove('dragging'); save(g); });
      (c.querySelector('h2') || c).appendChild(handle);
    });
    g.addEventListener('dragover', e => {
      e.preventDefault();
      const dragging = g.querySelector('.dragging'); if (!dragging) return;
      const after = [...g.querySelectorAll('.card:not(.dragging)')].find(el => {
        const b = el.getBoundingClientRect();
        return e.clientY < b.top + b.height/2 || (e.clientY < b.bottom && e.clientX < b.left + b.width/2);
      });
      after ? g.insertBefore(dragging, after) : g.appendChild(dragging);
    });
  });

  function save(g) {
    const s = store(); s[gkey(g)] = [...g.children].map(c => c.dataset.rkey);
    localStorage.setItem(LS_KEY, JSON.stringify(s));
  }
  const saved = store();
  groups().forEach(g => {
    const order = saved[gkey(g)]; if (!order) return;
    const byKey = new Map([...g.children].map(c => [c.dataset.rkey, c]));
    // Unknown keys are cards shipped after the layout was saved — append them rather than drop them,
    // so a new panel never disappears for someone with a stored layout.
    order.forEach(k => byKey.has(k) && g.appendChild(byKey.get(k)));
  });
  $('#reset-layout').onclick = () => {
    localStorage.removeItem(LS_KEY);
    groups().forEach(g => (srcOrder.get(gkey(g))||[]).forEach(c => g.appendChild(c)));
  };
})();
"""

HTML = (
    "<!doctype html><meta charset='utf-8'><title>Flies — paper</title>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    f"<style>{_STYLE}</style>{_BODY}<script>{_JS}</script>"
)


# --------------------------------------------------------------------------- server
class _ThreadingServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def _handler_for(db_path: str | None):
    class _Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's interface
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                self._send(HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/data":
                query = parse_qs(parsed.query)
                conn = dbmod.connect(db_path)
                try:
                    payload = build_api_data(conn, query.get("date", [None])[0],
                                             query.get("arm", [None])[0])
                except Exception as exc:  # a broken panel should not take the page down
                    payload = {"ok": False, "error": str(exc)}
                finally:
                    conn.close()
                self._send(json.dumps(payload, default=str).encode("utf-8"), "application/json")
                return
            self._send(b"not found", "text/plain", 404)

        def log_message(self, *args):
            pass  # a poll every 15s would otherwise flood the module log

    return _Handler


def serve(port: int, db_path: str | None = None, open_browser: bool = True) -> int:
    if port_in_use(port):
        print(f"already serving on http://{HOST}:{port}")
        if open_browser:
            webbrowser.open(f"http://{HOST}:{port}/")
        return 0
    server = _ThreadingServer((HOST, port), _handler_for(db_path))
    print(f"flies dashboard on http://{HOST}:{port}  (loopback only, read-only)")
    if open_browser:
        webbrowser.open(f"http://{HOST}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="cherrypick-flies read-only dashboard")
    ap.add_argument("--port", type=int)
    ap.add_argument("--db")
    ap.add_argument("--no-browser", action="store_true",
                    help="don't open a browser tab on start (for headless/background launches)")
    ap.add_argument("--json", action="store_true", help="print one API payload and exit")
    args = ap.parse_args(argv)

    if args.json:
        conn = dbmod.connect(args.db)
        try:
            print(json.dumps(build_api_data(conn), indent=2, default=str))
        finally:
            conn.close()
        return 0
    return serve(resolve_port(args.port), args.db, open_browser=not args.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())
