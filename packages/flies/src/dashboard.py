"""Read-only dashboard for cherrypick-flies — loopback HTTP, no build step.

Mirrors `packages/meic/src/dashboard.py`: one stdlib `http.server`, one HTML string, two routes
(`/` and `/api/data`). It reads the paper database and nothing else — no broker, no network, no
decisions — so nothing here can touch the loop-decision guardrail.

**Bound to 127.0.0.1 deliberately.** These pages show P&L, strikes, and the full decision journal with
no authentication. The orchestrator reaches it by iframe on the same host.

Three views:
  Today        the payoff curve (the profit forest itself), open positions with their floors, the
               decision journal, and the day's data quality.
  History      filterable trade log, per-arm comparison, daily heatmap, entry windows, fee drag.
  Performance  P&L over daily/weekly/monthly, completion rate and latency, arm divergence.

Every number comes from `analytics.py`, so no figure here can disagree with the EOD report or the
suite card.
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
.heat{display:flex;flex-wrap:wrap;gap:3px}
.hcell{width:16px;height:16px;border-radius:3px;background:#21262d}
.note{color:var(--dim);font-size:11.5px;margin-top:10px;line-height:1.5}
.pill{font-size:10.5px;padding:1px 7px;border-radius:9px;border:1px solid var(--line);color:var(--dim)}
.pill.ok{color:var(--pos);border-color:#1f6f33}
.pill.bad{color:var(--neg);border-color:#6f2420}
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
      <div class="card" style="grid-column:1/-1"><h2>Payoff at expiry — the profit forest</h2>
        <canvas id="payoff" height="150"></canvas>
        <div class="note" id="payoff-note"></div></div>
      <div class="card"><h2>Positions</h2><div class="scroll"><table id="pos-tbl"></table></div></div>
      <div class="card"><h2>Book floors</h2><div class="scroll"><table id="book-tbl"></table></div></div>
      <div class="card" style="grid-column:1/-1"><h2>Decision journal — why we did or didn't trade</h2>
        <div class="scroll"><table id="journal-tbl"></table></div>
        <div class="note">Repeated refusals are collapsed into one counted run, so a quiet day reads as
        a few rows that explain themselves rather than hundreds of identical ones.</div></div>
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
      <div class="card" style="grid-column:1/-1"><h2>Daily P&amp;L</h2><div class="heat" id="heat"></div></div>
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

/* The payoff curve: filled green above zero, red below. This is the view the strategy is named for —
   the shape tells you instantly whether a book is genuinely safe or merely safe-looking. */
function drawPayoff(cv, curve, spot) {
  const {g, w, h} = prep(cv);
  if (!curve || curve.empty || !curve.prices.length) {
    g.fillStyle = '#8b949e'; g.font = '13px system-ui';
    g.fillText('No positions yet today.', 12, h/2); return;
  }
  const pad = {l: 58, r: 12, t: 12, b: 22};
  const xs = curve.prices, ys = curve.pnl;
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  let yMin = Math.min(...ys, 0), yMax = Math.max(...ys, 0);
  const span = (yMax - yMin) || 1; yMin -= span*0.1; yMax += span*0.1;
  const X = v => pad.l + (v - xMin) / ((xMax - xMin)||1) * (w - pad.l - pad.r);
  const Y = v => h - pad.b - (v - yMin) / ((yMax - yMin)||1) * (h - pad.t - pad.b);
  const zero = Y(0);

  // zero line + y labels
  g.strokeStyle = '#252c36'; g.lineWidth = 1;
  g.beginPath(); g.moveTo(pad.l, zero); g.lineTo(w - pad.r, zero); g.stroke();
  g.fillStyle = '#8b949e'; g.font = '10px system-ui';
  [yMax, 0, yMin].forEach(v => g.fillText(fmtMoney(v), 4, Y(v) + 3));

  // fill above/below zero separately so the profitable band reads at a glance
  [[1,'rgba(63,185,80,.28)'],[-1,'rgba(248,81,73,.24)']].forEach(([sign, fill]) => {
    g.beginPath(); g.moveTo(X(xs[0]), zero);
    xs.forEach((x,i) => {
      const y = sign > 0 ? Math.max(ys[i], 0) : Math.min(ys[i], 0);
      g.lineTo(X(x), Y(y));
    });
    g.lineTo(X(xs[xs.length-1]), zero); g.closePath();
    g.fillStyle = fill; g.fill();
  });

  g.beginPath(); xs.forEach((x,i) => i ? g.lineTo(X(x), Y(ys[i])) : g.moveTo(X(x), Y(ys[i])));
  g.strokeStyle = '#58a6ff'; g.lineWidth = 1.6; g.stroke();

  (curve.centers||[]).forEach(c => {
    g.strokeStyle = 'rgba(139,148,158,.45)'; g.setLineDash([3,3]); g.beginPath();
    g.moveTo(X(c), pad.t); g.lineTo(X(c), h - pad.b); g.stroke(); g.setLineDash([]);
  });
  if (spot) {
    g.strokeStyle = '#d29922'; g.beginPath();
    g.moveTo(X(spot), pad.t); g.lineTo(X(spot), h - pad.b); g.stroke();
  }
  g.fillStyle = '#8b949e';
  g.fillText(fmtNum(xMin,0), pad.l, h - 6);
  g.fillText(fmtNum(xMax,0), w - pad.r - 34, h - 6);
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

  const arm = ARM === 'ALL' ? (d.arms[0] || null) : ARM;
  const curve = arm ? t.curves[arm] : null;
  const spot = (t.positions.find(p => p.underlying_at_entry) || {}).underlying_at_entry;
  drawPayoff($('#payoff'), curve, spot);
  const f = curve && curve.floor;
  $('#payoff-note').textContent = !f ? 'Select an arm with positions to see its curve.'
    : (f.floor_holds
       ? `Floor ${fmtMoney(f.worst)} — holds at every price. This book cannot lose.`
       : `Worst case ${fmtMoney(f.worst)} at ${fmtNum(f.worst_at,0)}` +
         (f.band ? ` · profitable between ${fmtNum(f.band[0],0)} and ${fmtNum(f.band[1],0)}` : '') +
         (f.unbounded_below ? ' · loses outside that band' : ''));

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

  const days = h.daily, max = Math.max(1, ...days.map(x => Math.abs(x.net_pnl||0)));
  $('#heat').innerHTML = days.length ? days.map(x => {
    const v = x.net_pnl || 0, a = Math.min(1, Math.abs(v)/max)*0.85 + 0.15;
    const col = v > 0 ? `rgba(63,185,80,${a})` : v < 0 ? `rgba(248,81,73,${a})` : '#21262d';
    return `<div class="hcell" style="background:${col}" title="${x.date}: ${fmtMoney(v)} (${x.trades} trades)"></div>`;
  }).join('') : '<span class="empty">No settled days yet.</span>';

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


def serve(port: int, db_path: str | None = None, open_browser: bool = False) -> int:
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
    ap.add_argument("--open", action="store_true", help="open a browser tab")
    ap.add_argument("--json", action="store_true", help="print one API payload and exit")
    args = ap.parse_args(argv)

    if args.json:
        conn = dbmod.connect(args.db)
        try:
            print(json.dumps(build_api_data(conn), indent=2, default=str))
        finally:
            conn.close()
        return 0
    return serve(resolve_port(args.port), args.db, args.open)


if __name__ == "__main__":
    raise SystemExit(main())
