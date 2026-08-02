"""Read-only dashboard for cherrypick-flies — loopback HTTP, no build step.

Mirrors `packages/meic/src/dashboard.py`: one stdlib `http.server`, one HTML string, two routes
(`/` and `/api/data`). It reads the paper database and nothing else — no broker, no network, no
decisions — so nothing here can touch the loop-decision guardrail.

**Bound to 127.0.0.1 deliberately.** These pages show P&L, strikes, and the full decision journal with
no authentication. The orchestrator reaches it by iframe on the same host.

Three views:
  Today        the payoff curve (the profit forest itself), the session timeline, open positions with
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
import re
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

from cherrypick.core import viz  # noqa: E402

from cherrypick.flies import analytics  # noqa: E402
from cherrypick.flies import db as dbmod  # noqa: E402

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
    The orchestrator's embed `ensure_server` relaunches freely, and this is what makes that safe.
    The probe itself is the suite's one copy in cherrypick.core.viz."""
    return viz.port_in_use(port, host)


def build_api_data(conn, day: str | None = None, arm: str | None = None, symbol: str | None = None) -> dict:
    """Everything all three views need, in one payload — the client filters locally from here."""
    day = day or analytics.today()
    arm_filter = None if not arm or arm == "ALL" else arm
    symbol_filter = None if not symbol or symbol == "ALL" else symbol
    # The arm/symbol ROSTER is built from today's UNFILTERED books (an arm/symbol trading today but
    # with nothing settled yet wouldn't appear in by_arm's settled-only view) — never from the
    # filtered overview below, or picking one arm would collapse the other selector's own options.
    today_books_unfiltered = analytics.books_for_day(conn, day)
    overview = analytics.session_overview(conn, day, arm=arm_filter, symbol=symbol_filter)

    arms = sorted(
        {b["arm"] for b in today_books_unfiltered} | {r["arm"] for r in analytics.by_arm(conn) if r["arm"]}
    )
    # The symbol roster: distinct underlyings ever recorded, from both today's books and the full
    # trade log — so the selector offers XSP (current) and SPX (retired, both books remain in the
    # ledger) even on a day that only traded one of them.
    symbols = sorted(
        {b["symbol"] for b in today_books_unfiltered if b.get("symbol")}
        | {r["symbol"] for r in analytics.trade_log(conn) if r.get("symbol")}
    )
    curves = {a: analytics.payoff_curve(conn, day, a) for a in arms} or {}

    return {
        "ok": True,
        "generated_at": analytics.clock.now_iso(),
        "date": day,
        "arms": arms,
        "selected_arm": arm or "ALL",
        "symbols": symbols,
        "selected_symbol": symbol or "ALL",
        "today": {
            "stats": overview["stats"],
            "books": overview["books"],
            "positions": overview["positions"],
            "open_count": overview["open_count"],
            "fly_count": overview["fly_count"],
            "risk_free_count": overview["risk_free_count"],
            "max_possible_loss": overview["max_possible_loss"],
            "completion": overview["completion"],
            "divergence": overview["divergence"],
            "journal": overview["journal"],
            "curves": curves,
            "timeline": analytics.session_timeline(conn, day),
        },
        "history": {
            "trades": analytics.trade_log(conn, arm=arm_filter, symbol=symbol_filter),
            "by_arm": analytics.by_arm(conn, symbol=symbol_filter),
            # What by_arm held back, so the arm table summing below the book total is explained on
            # the page rather than left as an unexplained gap.
            "arm_exclusions": analytics.arm_comparison_exclusions(conn, symbol=symbol_filter),
            "by_entry_mode": analytics.by_entry_mode(conn, symbol=symbol_filter),
            "by_window": analytics.by_entry_window(conn, symbol=symbol_filter),
            "fee_drag": analytics.fee_drag(conn, symbol=symbol_filter),
            "daily": analytics.daily_pnl(conn, arm=arm_filter, symbol=symbol_filter),
        },
        "performance": {
            "daily": analytics.pnl_series(conn, "daily", arm=arm_filter, symbol=symbol_filter),
            "weekly": analytics.pnl_series(conn, "weekly", arm=arm_filter, symbol=symbol_filter),
            "monthly": analytics.pnl_series(conn, "monthly", arm=arm_filter, symbol=symbol_filter),
            "all_time": analytics.stats_for_period(conn, arm=arm_filter, symbol=symbol_filter),
            "completion": analytics.completion_stats(conn, symbol=symbol_filter),
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
.badge.live{background:#7f1d1d;color:#fecaca;font-weight:700;letter-spacing:.03em}
nav{display:flex;gap:4px;margin-left:auto;flex-wrap:wrap}
nav button{background:transparent;border:1px solid var(--line);color:var(--dim);padding:5px 12px;
border-radius:6px;cursor:pointer;font-size:13px}
nav button.active{background:var(--panel);color:var(--fg);border-color:var(--accent)}
select,input{background:var(--panel);border:1px solid var(--line);color:var(--fg);padding:4px 8px;
border-radius:6px;font-size:13px}
main{padding:18px 20px 60px}
.view{display:none}.view.active{display:block}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px}
/* Positions / Book floors / Arm divergence share one row at a 3:3:2 width ratio -- scoped to the
   Today grid only, so History/Performance keep the plain auto-fit layout above. The full-width
   cards (Payoff/Timeline/Journal) keep their own inline grid-column:1/-1, which wins over this
   default regardless of specificity; Arm divergence overrides the default via its own inline style. */
#view-today .grid{grid-template-columns:repeat(8,1fr)}
#view-today .grid>.card{grid-column:span 3}
@media (max-width:900px){#view-today .grid{grid-template-columns:1fr}}
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
.f-clear{background:none;border:1px solid var(--line);color:var(--dim);border-radius:4px;
padding:3px 8px;font-size:11px;cursor:pointer}
.f-clear:hover{color:var(--fg);border-color:var(--accent)}
canvas{width:100%!important}
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
  <span class="badge" id="mode-badge">paper</span>
  <span class="badge" id="asof">–</span>
  <label class="dim" style="font-size:12px">source
    <select id="source-select"><option value="paper">paper</option><option value="live">live — real money</option></select>
  </label>
  <label class="dim" style="font-size:12px">arm
    <select id="arm-select"><option value="ALL">all</option></select>
  </label>
  <label class="dim" style="font-size:12px">symbol
    <select id="symbol-select"><option value="ALL">all</option></select>
  </label>
  <label class="dim" style="font-size:12px">x-axis width
    <select id="xwidth-select">
      <option value="auto">auto</option>
      <option value="50">50</option>
      <option value="100">100</option>
      <option value="500">500</option>
      <option value="1000">1000</option>
    </select>
  </label>
  <label class="dim" style="font-size:12px">y-axis range
    <select id="ywidth-select">
      <option value="auto">auto</option>
      <option value="250">$250</option>
      <option value="500">$500</option>
      <option value="1000">$1,000</option>
      <option value="5000">$5,000</option>
    </select>
  </label>
  <nav>
    <button data-view="today" class="active">Today</button>
    <input type="date" id="today-date" style="display:none" title="View the profit forest for a previous session">
    <button id="today-date-clear" class="f-clear" style="display:none">back to today</button>
    <button data-view="history">History</button>
    <button data-view="performance">Performance</button>
  </nav>
</header>
<main data-cp-reorder-store='flies-dash-layout-v1'>
  <section class="view active" id="view-today">
    <div class="tiles" id="today-tiles"></div>
    <div class="grid" data-cp-reorder="view-today" data-cp-reorder-items=".card">
      <div class="card" style="grid-column:1/-1"><h2>Payoff at expiry — the profit forest</h2>
        <canvas id="payoff" height="260"></canvas>
        <div class="legend" id="payoff-legend"></div>
        <div class="note" id="payoff-note"></div></div>
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
      <div class="card" style="grid-column:1/-1"><h2>Decision journal — why we did or didn't trade</h2>
        <canvas id="journal-gantt" height="120"></canvas>
        <div class="legend" id="journal-legend"></div>
        <div class="scroll"><table id="journal-tbl"></table></div>
        <div class="note">Repeated refusals are collapsed into one counted run, so a quiet day reads as
        a few rows that explain themselves rather than hundreds of identical ones. The strip draws each
        run as a bar over the span it held — a gate that blocked all morning is a bar covering the
        morning, next to the brief green marks where an entry actually fired.</div></div>
      <div class="card"><h2>Positions</h2><div class="scroll"><table id="pos-tbl"></table></div></div>
      <div class="card"><h2>Book floors</h2><div class="scroll"><table id="book-tbl"></table></div></div>
      <div class="card" style="grid-column:span 2"><h2>Arm divergence</h2><div class="scroll"><table id="div-tbl"></table></div>
        <div class="note" id="div-note"></div></div>
    </div>
  </section>

  <section class="view" id="view-history">
    <div class="grid" data-cp-reorder="view-history" data-cp-reorder-items=".card">
      <div class="card"><h2>By arm</h2><div class="scroll"><table id="arm-tbl"></table></div></div>
      <div class="card"><h2>By entry mode</h2><div class="scroll"><table id="mode-tbl"></table></div></div>
      <div class="card"><h2>By entry window</h2><div class="scroll"><table id="win-tbl"></table></div>
        <div class="note">Windows are unranked by design — the ranking is meant to emerge here.</div></div>
      <div class="card"><h2>Fee drag</h2><div class="scroll"><table id="fee-tbl"></table></div>
        <div class="note">A legged fly pays two fee stacks against a credit that may be $35–105.
        Costs are not a rounding error for this strategy.</div></div>
      <div class="card" style="grid-column:1/-1"><h2>Daily P&amp;L</h2><div id="heat"></div>
        <div class="note">A settled trading day per cell, Monday at the top of each week column. An
        empty cell is a session that never settled, not a flat one — the two are different findings.</div></div>
      <div class="card" style="grid-column:1/-1"><h2>Trade log</h2>
        <div class="filters">
          <select id="f-outcome"><option value="">all outcomes</option><option>win</option>
            <option>loss</option><option>pinned</option><option>risk-free</option></select>
          <input id="f-search" placeholder="search all columns…">
          <button class="f-clear" id="f-clear">clear filters</button>
          <span class="dim" id="f-count"></span>
        </div>
        <div class="scroll"><table id="log-tbl"></table></div>
        <div class="note">Every column filters in its own header cell — text columns match on
        substring, numeric and date columns take a min/max. They combine, so the count is what
        survived all of them. Outcome and the search box above span every column.</div></div>
    </div>
  </section>

  <section class="view" id="view-performance">
    <div class="tiles" id="perf-tiles"></div>
    <div class="grid" data-cp-reorder="view-performance" data-cp-reorder-items=".card">
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
let DATA = null, ARM = 'ALL', SYMBOL = 'ALL', XWIDTH = 'auto', YWIDTH = 'auto', SOURCE = 'paper';
// null means "today" -- left for the server to resolve (analytics.today() is the ET session date,
// not the browser's local date, which can already be tomorrow west of Eastern). Only the Today
// view is date-scoped server-side; History/Performance always show the full range regardless.
let DATE = null;
// The day's settlement print once the session has settled, else null -- see the Today view's
// spot handling for why settlement beats the last intraday tick as the headline price.
let SETTLE_PX = null;

/* ---------- per-column filters ----------

   A column opts in by declaring `filter`:
     'select'    distinct values, gathered from the data so a new arm/window/mode appears by itself
     'text'      case-insensitive substring
     'range'     numeric min/max
     'daterange' ISO date min/max

   `c.v(row)` supplies the RAW value to filter on, because `c.f(row)` returns display markup (money
   strings, pills) that would never compare correctly. Falls back to c.f when a column has no v.

   State lives in a caller-owned object rather than in the DOM: every render replaces innerHTML, so
   anything read back off the inputs is gone the moment the 30s refresh fires mid-typing. */
/* The filterable-table component lives in cherrypick.core.viz (this page's version was the
   donor). `table` is a local alias for the one function TABLE_JS only ever assigns onto
   `window` (no naming collision). `matchesFilters`/`filterActive` are NOT aliased the same
   way -- TABLE_JS declares those as bare top-level `function` statements of the same name, so
   a `const matchesFilters = ...` here would try to redeclare an existing global with `const`,
   which is a SyntaxError that silently killed this entire script (nothing below ever ran,
   including every Today/History/Performance render). Both names already exist as callable
   globals once TABLE_JS's <script> tag has run, so no local alias is needed at all. */
const table = window.cpTable;

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
const SPOT_COLOR = '#e3b341';

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

/* A position's payoff is genuinely flat beyond its own scanned range (fly_payoff and
   short_vertical_payoff both saturate there) -- so when the axis window is wider than one
   curve's own price array (a default-width window next to a narrow-wing arm, say), the flat
   floor must carry all the way to the window's edges at its own boundary value. Without this
   the line -- and the single-arm fill, which is the "this book cannot lose here" claim --
   stops wherever that arm's own data happened to end, which understates the floor rather than
   drawing it. */
function extendFlat(xs, ys, xMin, xMax) {
  const ex = xs.slice(), ey = ys.slice();
  if (ex[0] > xMin) { ex.unshift(xMin); ey.unshift(ey[0]); }
  if (ex[ex.length-1] < xMax) { ex.push(xMax); ey.push(ey[ey.length-1]); }
  return {xs: ex, ys: ey};
}

/* The min/max payoff actually visible within [xMin, xMax] -- NOT the curve's full scanned
   range, which can run well past what the current x-window shows (see extendFlat above). Falls
   back to the flat boundary value when the window sits entirely outside this curve's own data
   (a narrow-wing arm under a wide fixed x-width, say), so a curve with nothing literally inside
   the window still reports its real (flat) value rather than an empty range. */
function visibleYRange(xs, ys, xMin, xMax) {
  let mn = Infinity, mx = -Infinity;
  xs.forEach((x,i) => { if (x >= xMin && x <= xMax) { mn = Math.min(mn, ys[i]); mx = Math.max(mx, ys[i]); } });
  if (mn === Infinity) {
    const ext = extendFlat(xs, ys, xMin, xMax);
    mn = mx = ext.ys[0];
  }
  return {min: mn, max: mx};
}

const minuteOf = ts => {
  const hm = String(ts||'').slice(11,16);
  return (+hm.slice(0,2)) * 60 + (+hm.slice(3,5));
};
const hhmm = m => String(Math.floor(m/60)).padStart(2,'0') + ':' + String(Math.round(m%60)).padStart(2,'0');

/* Hover state is per canvas, keyed by element id, so a crosshair on one chart never redraws the
   other. Each chart registers a redraw thunk and gets a crosshair plus a readout for free.

   The mousemove/mouseleave LISTENERS are attached only once per canvas (hoverBound guards that —
   addEventListener would otherwise stack a new one on every render). But bindHover() itself runs
   on every render with a FRESH closure over that render's data, so the redraw function actually
   invoked must be looked up at event time, not captured once at bind time -- otherwise the very
   first bind (page load, source=paper) wins forever and hovering after any later refresh, or after
   switching the source/arm/symbol selector, silently redraws with that first render's stale data. */
const HOVER = {};
const REDRAW = {};
function bindHover(cv, redraw) {
  REDRAW[cv.id] = redraw;
  if (cv.dataset.hoverBound) return;
  cv.dataset.hoverBound = '1'; cv.classList.add('hoverable');
  cv.addEventListener('mousemove', e => {
    const b = cv.getBoundingClientRect();
    HOVER[cv.id] = {x: e.clientX - b.left, y: e.clientY - b.top}; REDRAW[cv.id]();
  });
  cv.addEventListener('mouseleave', () => { delete HOVER[cv.id]; REDRAW[cv.id](); });
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
function drawPayoff(cv, curves, arms, selected, spot, xwidth, ywidth, spotLabel) {
  const {g, w, h} = prep(cv);
  const shown = (selected === 'ALL' ? arms : [selected])
    .filter(a => curves[a] && !curves[a].empty && curves[a].prices.length);
  if (!shown.length) {
    g.fillStyle = '#8b949e'; g.font = '13px system-ui';
    g.fillText('No positions yet today.', 12, h/2); return;
  }
  const pad = {l: 62, r: 12, t: 12, b: 26};
  // The x-axis is centred on the book's own centres (not each curve's full scanned price
  // array, which pads +/-3x that arm's wing width beyond its outermost centre -- a wide-wing
  // arm shown alongside narrow ones used to stretch the whole axis into mostly flat deadspace).
  // `xwidth` picks the window: 'auto' is exactly the trades' own span plus a small buffer;
  // a fixed width (50/100/500/1000) is a MINIMUM, not an override -- either way the window still
  // only grows, never shrinks past that floor, and only as far as needed to keep every centre
  // AND the current spot inside it. It never clips off where the market actually is.
  let cMin = Infinity, cMax = -Infinity;
  shown.forEach(a => {
    (curves[a].centers && curves[a].centers.length ? curves[a].centers : curves[a].prices).forEach(k => {
      cMin = Math.min(cMin, k); cMax = Math.max(cMax, k);
    });
  });
  if (spot != null) { cMin = Math.min(cMin, spot); cMax = Math.max(cMax, spot); }
  const mid = (cMin + cMax) / 2;
  const naturalHalf = (cMax - cMin) / 2 + 2;
  const half = (!xwidth || xwidth === 'auto') ? naturalHalf : Math.max(Number(xwidth) / 2, naturalHalf);
  let xMin = mid - half, xMax = mid + half;

  // The y-axis fits what's actually VISIBLE in that x-window, not each curve's full price array --
  // a wide-wing arm's deep worst-case at a price the x-window no longer shows must not still
  // inflate the y-scale and waste vertical space on a value nothing on screen reaches. `ywidth`
  // works like `xwidth` but independently per side (not forced symmetric): a fixed tier is a
  // MINIMUM for the downside and the upside separately, since a book's floor and its peak are
  // rarely the same distance from zero and mirroring the smaller side just wastes space.
  let yLo = 0, yHi = 0;
  shown.forEach(a => {
    const r = visibleYRange(curves[a].prices, curves[a].pnl, xMin, xMax);
    yLo = Math.min(yLo, r.min); yHi = Math.max(yHi, r.max);
  });
  if (ywidth && ywidth !== 'auto') {
    const ybound = Number(ywidth);
    yLo = Math.min(yLo, -ybound); yHi = Math.max(yHi, ybound);
  }
  const span = (yHi - yLo) || 1; let yMin = yLo - span*0.1, yMax = yHi + span*0.1;
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
    const c = curves[shown[0]];
    const {xs, ys} = extendFlat(c.prices, c.pnl, xMin, xMax);
    [[1,'rgba(63,185,80,.28)'],[-1,'rgba(248,81,73,.24)']].forEach(([sign, fill]) => {
      g.beginPath(); g.moveTo(X(xs[0]), zero);
      xs.forEach((x,i) => g.lineTo(X(x), Y(sign > 0 ? Math.max(ys[i],0) : Math.min(ys[i],0))));
      g.lineTo(X(xs[xs.length-1]), zero); g.closePath();
      g.fillStyle = fill; g.fill();
    });
  }

  shown.forEach(a => {
    const c = curves[a], col = armColor(a, arms);
    const {xs, ys} = extendFlat(c.prices, c.pnl, xMin, xMax);
    g.beginPath();
    xs.forEach((x,i) => i ? g.lineTo(X(x), Y(ys[i])) : g.moveTo(X(x), Y(ys[i])));
    g.strokeStyle = col; g.lineWidth = shown.length === 1 ? 1.8 : 1.4; g.stroke();
    (c.centers||[]).forEach(k => {
      g.strokeStyle = col; g.globalAlpha = .35; g.setLineDash([3,3]);
      g.beginPath(); g.moveTo(X(k), pad.t); g.lineTo(X(k), h - pad.b); g.stroke();
      g.setLineDash([]); g.globalAlpha = 1;
    });
  });

  if (spot) {
    g.strokeStyle = SPOT_COLOR; g.lineWidth = 2; g.globalAlpha = .85;
    g.beginPath(); g.moveTo(X(spot), pad.t); g.lineTo(X(spot), h - pad.b); g.stroke();
    g.globalAlpha = 1;
    // The line alone doesn't say what "here" is -- label it with the actual spot price, clamped
    // inside the plot area so it never clips off either edge.
    const label = `${spotLabel || 'spot'} ${fmtNum(spot)}`, lp = 5;
    g.font = '11px system-ui';
    const lw = g.measureText(label).width;
    const lx = Math.min(Math.max(X(spot) - lw/2 - lp, pad.l), w - pad.r - lw - lp*2);
    g.fillStyle = 'rgba(13,17,23,.92)'; g.strokeStyle = '#3d4653';
    g.beginPath(); g.roundRect(lx, pad.t + 2, lw + lp*2, 16, 4); g.fill(); g.stroke();
    g.fillStyle = SPOT_COLOR; g.fillText(label, lx + lp, pad.t + 13);
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
    {c: SPOT_COLOR, t: SETTLE_PX != null ? 'settlement' : 'spot now'},
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
  // Fixed to the regular session (9:30-16:00 ET), not the recorded ticks' own min/max — SPX/XSP
  // both trade RTH only. A session that starts late or stops early should look short against a
  // fixed axis, not stretch to fill it and read as a normal, complete day.
  const tMin = 9*60 + 30, tMax = 16*60;
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
  // The shared week-column calendar (cherrypick.core.viz) — this page's layout was the donor,
  // so nothing changes visually; an empty weekday still means 'no settled session', not flat.
  if (!window.cpCalHeat($('#heat'), days, fmtMoney)) {
    $('#heat').innerHTML = '<span class="empty">No settled days yet.</span>';
  }
}

/* ---------- renderers ---------- */
function renderToday(d) {
  const t = d.today, s = t.stats, c = t.completion;
  // Every figure here -- tiles, positions, books -- is already narrowed to the selected arm and
  // symbol server-side (analytics.session_overview), so the whole card tells one consistent story
  // for whatever scope is picked, the same scope the payoff curve below draws.
  const posRows = t.positions, bookRows = t.books;
  tiles($('#today-tiles'), [
    {k:'Net P&L', v:fmtMoney(s.net_pnl), t:tone(s.net_pnl)},
    {k:'Positions', v:posRows.length},
    {k:'Open', v:t.open_count},
    {k:'Risk-free', v:t.risk_free_count, t:t.risk_free_count?'pos':''},
    {k:'Completion', v:fmtPct(c.completion_rate)},
    {k:'Fees', v:fmtMoney(s.fees), t:'dim'},
    // Every open position's own worst case (full defined risk for a short vertical, 0 for a
    // fly) net of trading fees AND the worst-case $5/contract exercise-assignment fee, as if
    // every leg finished ITM -- see fly.position_floor. Zero means nothing open can still lose.
    {k:'Max possible loss', v:fmtMoney(t.max_possible_loss), t:t.max_possible_loss<0?'neg':'dim'},
  ]);

  const lastTick = ((t.timeline || {}).ticks || []).filter(x => x.spot != null).slice(-1)[0];
  const lastSpot = lastTick ? lastTick.spot
    : (t.positions.find(p => p.underlying_at_entry) || {}).underlying_at_entry;
  // Once the session has SETTLED, the number that decides every payoff is the settlement print,
  // not the last intraday tick -- and they can differ materially (2026-07-31, a month-end Friday:
  // last tick 750.46 vs a 748.97 close, which was the difference between one fly paying nothing
  // and paying near its max). Showing the intraday tick as the headline "spot" beside settled
  // positions invited exactly that misread, so settlement wins here and is labelled as such.
  const settled = (t.books || []).filter(b => b.status === 'settled' && b.settlement_price != null);
  SETTLE_PX = settled.length ? settled[0].settlement_price : null;
  const spot = SETTLE_PX != null ? SETTLE_PX : lastSpot;
  const spotLabel = SETTLE_PX != null ? 'settled' : 'spot';
  drawPayoff($('#payoff'), t.curves, d.arms, ARM, spot, XWIDTH, YWIDTH, spotLabel);
  drawTimeline($('#timeline'), t.timeline, ARM);

  // The feed's own report card: how many ticks actually built a snapshot, and what refused the rest.
  // A low build rate reframes a flat day as a thin-data day — the reading CLAUDE.md promises but that
  // only the module log could give before this.
  const fs = (t.timeline || {}).feed_summary;
  $('#timeline-feed').innerHTML = !fs || !fs.ticks ? ''
    : `Feed: ${fs.ok}/${fs.ticks} ticks built a snapshot (${fmtPct(fs.ok_rate)})` +
      (fs.refused ? ' · refused ' + fs.refused + ': ' +
        Object.entries(fs.by_reason).map(([k,v]) => `${k} ×${v}`).join(', ') : ' · no refusals');
  bindHover($('#payoff'), () => drawPayoff($('#payoff'), t.curves, d.arms, ARM, spot, XWIDTH, YWIDTH, spotLabel));
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

  // Once settled, say so explicitly -- with the provenance, and with the last intraday tick
  // alongside when it differs. A settlement struck away from the last tick is ordinary (the
  // closing auction moves the print, especially at month end), but it decides every payoff
  // above, so it should never be something the reader has to infer.
  if (SETTLE_PX != null && armsShown.length) {
    const src = (settled[0].settlement_source || 'unknown').replace(/_/g, ' ');
    const drift = lastSpot != null ? SETTLE_PX - lastSpot : null;
    $('#payoff-note').innerHTML +=
      `<br><span class="dim">Settled at <b>${fmtNum(SETTLE_PX,2)}</b> (${src})` +
      (drift != null && Math.abs(drift) >= 0.01
        ? ` — last intraday tick was ${fmtNum(lastSpot,2)}, ${fmtNum(Math.abs(drift),2)} ` +
          `${drift > 0 ? 'below' : 'above'} the close.`
        : '.') + '</span>';
  }

  const posEmpty = ARM === 'ALL' && SYMBOL === 'ALL' ? 'No positions today.'
    : `No ${[ARM, SYMBOL].filter(v => v !== 'ALL').join(' ')} positions today.`;
  table($('#pos-tbl'), [
    {h:'Symbol', f:r=>r.symbol}, {h:'Arm', f:r=>r.arm}, {h:'Mode', f:r=>r.entry_mode},
    {h:'Kind', f:r=>r.kind === 'fly' ? 'fly' : `short ${r.side}`},
    {h:'Centre', f:r=>fmtNum(r.center,0), num:1},
    {h:'Net', f:r=>fmtNum(r.net), num:1},
    {h:'Floor', f:r=>fmtMoney(r.floor_dollars), num:1, tone:r=>tone(r.floor_dollars)},
    {h:'', f:r=>r.risk_free ? '<span class="pill ok">risk-free</span>' :
        (r.kind==='fly'?'<span class="pill bad">floor negative</span>':'<span class="pill">at risk</span>')},
    {h:'Status', f:r=>r.status},
  ], posRows, posEmpty);

  table($('#book-tbl'), [
    {h:'Symbol', f:r=>r.symbol}, {h:'Arm', f:r=>r.arm},
    {h:'Credit', f:r=>fmtMoney(r.credit_collected), num:1},
    {h:'Debits', f:r=>fmtMoney(r.debits_paid), num:1},
    {h:'Fees', f:r=>fmtMoney(r.fees), num:1},
    {h:'Worst', f:r=>fmtMoney(r.worst), num:1, tone:r=>tone(r.worst)},
    {h:'Band', f:r=>r.band_low === null ? '–' : `${fmtNum(r.band_low,0)}–${fmtNum(r.band_high,0)}`},
    {h:'', f:r=>r.floor_holds ? '<span class="pill ok">holds</span>'
        : '<span class="pill bad">bounded</span>'},
  ], bookRows, ARM === 'ALL' && SYMBOL === 'ALL' ? 'No books today.' : 'No matching books today.');

  drawJournalGantt($('#journal-gantt'), t.journal);
  bindHover($('#journal-gantt'), () => drawJournalGantt($('#journal-gantt'), t.journal));
  table($('#journal-tbl'), [
    {h:'Arm', f:r=>r.arm}, {h:'Mode', f:r=>r.mode},
    {h:'Decision', f:r=>r.accepted ? `<span class="pill ok">${r.reason}</span>` : r.reason},
    {h:'consecutive rejection count', f:r=>r.occurrences, num:1},
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

/* Per-column filter state for the trade log, kept outside the DOM so the 30s auto-refresh can't wipe
   a half-typed filter. Keyed by column index. */
const LOG_FILTERS = {};

function logColumns() {
  return [
    {h:'Date', f:r=>r.trade_date, v:r=>r.trade_date, filter:'daterange'},
    {h:'Symbol', f:r=>r.symbol, v:r=>r.symbol, filter:'select'},
    {h:'Arm', f:r=>r.arm, v:r=>r.arm, filter:'select'},
    {h:'Mode', f:r=>r.entry_mode, v:r=>r.entry_mode, filter:'select'},
    {h:'Kind', f:r=>r.kind === 'fly' ? 'fly' : `short ${r.side}`,
     v:r=>r.kind === 'fly' ? 'fly' : `short ${r.side}`, filter:'select'},
    {h:'Centre', f:r=>fmtNum(r.center,0), v:r=>r.center, num:1, filter:'range'},
    {h:'Window', f:r=>r.entry_window || '–', v:r=>r.entry_window || '', filter:'select'},
    {h:'Net', f:r=>fmtNum(r.net), v:r=>r.net, num:1, filter:'range'},
    {h:'Fees', f:r=>fmtMoney(r.fees), v:r=>r.fees, num:1, filter:'range'},
    {h:'P&L', f:r=>fmtMoney(r.pnl), v:r=>r.pnl, num:1, tone:r=>tone(r.pnl), filter:'range'},
    {h:'Latency', f:r=>r.completion_latency_min === null ? '–' : r.completion_latency_min+'m',
     v:r=>r.completion_latency_min, num:1, filter:'range'},
    {h:'', f:r=>r.pinned ? '<span class="pill ok">pinned</span>' : ''},
  ];
}

function renderLog() {
  const cols = logColumns();
  const all = DATA.history.trades || [];
  const rows = all.filter(t => {
    // Outcome and search deliberately stay outside the column row: they span columns rather than
    // belonging to one (pinned/risk-free aren't displayed columns at all).
    const oc = $('#f-outcome').value;
    if (oc === 'win' && !(t.pnl > 0)) return false;
    if (oc === 'loss' && !(t.pnl < 0)) return false;
    if (oc === 'pinned' && !t.pinned) return false;
    if (oc === 'risk-free' && !t.risk_free) return false;
    const q = $('#f-search').value.trim().toLowerCase();
    if (q && !JSON.stringify(t).toLowerCase().includes(q)) return false;
    return matchesFilters(cols, t, LOG_FILTERS);
  });
  const filtered = rows.length !== all.length;
  $('#f-count').textContent = `${rows.length} trade${rows.length===1?'':'s'}` +
    (filtered ? ` of ${all.length}` : '');
  $('#f-clear').style.display =
    (filterActive(LOG_FILTERS) || $('#f-outcome').value || $('#f-search').value) ? '' : 'none';
  // allRows is the unfiltered set so each select keeps listing every value, not just the survivors.
  table($('#log-tbl'), cols, rows, 'No trades match these filters.',
        {state: LOG_FILTERS, onChange: renderLog, allRows: all});
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
    {k:'Blocked by fee_buffer', v:c.buffer_blocked},
    {k:'Blocked by min_floor_dollars', v:c.floor_blocked},
    {k:'Never priced', v:c.counterfactual_unknown},
  ]);
}

function renderAll(d) {
  DATA = d;
  const badge = $('#mode-badge');
  const isLive = d.source === 'live';
  badge.textContent = isLive ? 'LIVE — real money' : 'paper';
  badge.classList.toggle('live', isLive);
  $('#asof').textContent = `${d.date} · ${d.generated_at.slice(11,16)}`;
  // Keep the picker showing whatever the server actually resolved -- including the DATE===null
  // case, so the field displays today's real date without the user having to have touched it.
  $('#today-date').value = d.date;
  const sel = $('#arm-select');
  if (sel.options.length - 1 !== d.arms.length) {
    sel.innerHTML = '<option value="ALL">all</option>' +
      d.arms.map(a => `<option>${a}</option>`).join('');
    sel.value = ARM;
  }
  const symSel = $('#symbol-select');
  if (symSel.options.length - 1 !== (d.symbols||[]).length) {
    symSel.innerHTML = '<option value="ALL">all</option>' +
      (d.symbols||[]).map(s => `<option>${s}</option>`).join('');
    symSel.value = SYMBOL;
  }
  renderToday(d); renderHistory(d); renderPerformance(d);
}

async function refresh() {
  try {
    const dateParam = DATE ? `&date=${encodeURIComponent(DATE)}` : '';
    const r = await fetch(`/api/data?source=${encodeURIComponent(SOURCE)}&arm=${encodeURIComponent(ARM)}&symbol=${encodeURIComponent(SYMBOL)}${dateParam}`);
    const d = await r.json();
    if (d.ok) renderAll(d);
  } catch (e) { /* transient; the next tick retries */ }
}

document.querySelectorAll('nav button[data-view]').forEach(b => b.onclick = () => {
  document.querySelectorAll('nav button[data-view]').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  b.classList.add('active');
  $('#view-' + b.dataset.view).classList.add('active');
  // The date picker only means anything on the Today view (History/Performance always show the
  // full range) -- surfacing it here, on the click that lands on Today, is what makes "view a
  // previous day" discoverable without a dedicated always-on control cluttering the other tabs.
  const onToday = b.dataset.view === 'today';
  $('#today-date').style.display = onToday ? '' : 'none';
  $('#today-date-clear').style.display = (onToday && DATE) ? '' : 'none';
  if (DATA) renderAll(DATA);   // canvases size wrongly while hidden
});
$('#today-date').max = new Date().toISOString().slice(0, 10);
$('#today-date').onchange = e => {
  DATE = e.target.value || null;
  $('#today-date-clear').style.display = DATE ? '' : 'none';
  refresh();
};
$('#today-date-clear').onclick = () => {
  DATE = null;
  // Clear the picker's own value too -- it drives DATE via onchange, so leaving the old date
  // sitting there both looks like the click did nothing and would re-arm the same DATE if the
  // browser re-fires onchange (e.g. re-selecting the same day after navigating away and back).
  $('#today-date').value = '';
  $('#today-date-clear').style.display = 'none';
  refresh();
};
// Guarded: a paper-only server (the orchestrator's embed) strips this control from the page, and an
// unguarded `$('#source-select').onchange = ...` would throw on null and stop the whole script --
// taking the arm/symbol handlers and refresh() down with it, leaving a dead dashboard rather than a
// paper-only one.
const sourceSel = $('#source-select');
if (sourceSel) sourceSel.onchange = e => {
  SOURCE = e.target.value;
  // arms/symbols can differ between ledgers (today: live is pinned to one arm) -- a stale
  // selection from the other source would just render an empty page with no obvious reason.
  ARM = 'ALL'; SYMBOL = 'ALL';
  $('#arm-select').value = 'ALL'; $('#symbol-select').value = 'ALL';
  refresh();
};
$('#arm-select').onchange = e => { ARM = e.target.value; refresh(); };
$('#symbol-select').onchange = e => { SYMBOL = e.target.value; refresh(); };
$('#xwidth-select').onchange = e => { XWIDTH = e.target.value; if (DATA) renderAll(DATA); };
$('#ywidth-select').onchange = e => { YWIDTH = e.target.value; if (DATA) renderAll(DATA); };
// Date and mode moved into their own column cells; these two span columns and stay in the bar.
['#f-outcome','#f-search'].forEach(s => {
  $(s).oninput = renderLog; $(s).onchange = renderLog;
});
$('#f-clear').onclick = () => {
  Object.keys(LOG_FILTERS).forEach(k => delete LOG_FILTERS[k]);
  $('#f-outcome').value = ''; $('#f-search').value = '';
  renderLog();
};
$('#perf-gran').onchange = () => renderPerformance(DATA);
$('#perf-cum').onchange = () => renderPerformance(DATA);
window.addEventListener('resize', () => { if (DATA) renderAll(DATA); });

refresh();
setInterval(refresh, 15000);

/* drag-to-reorder lives in cherrypick.core.viz.REORDER_JS (the suite's one copy);
   groups are declared with data-cp-reorder attributes in the markup. */
"""


HTML = (
    "<!doctype html><meta charset='utf-8'><title>Flies</title>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    f"<style>{_STYLE}{viz.REORDER_STYLE}{viz.CAL_HEAT_STYLE}{viz.TABLE_STYLE}</style>{_BODY}"
    f"<script>{viz.CAL_HEAT_JS}</script><script>{viz.TABLE_JS}</script>"
    f"<script>{_JS}</script><script>{viz.REORDER_JS}</script>"
)


# --------------------------------------------------------------------------- server
class _ThreadingServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


#: Matches the whole source-selector <label> block so a paper-only server can strip it from the page.
#: A regex rather than an exact string: the markup carries an em dash and its own indentation, and
#: pinning those byte-for-byte means the strip silently stops working the next time the template is
#: reformatted -- failing open, which is the wrong direction for this particular control. Removing it
#: is cosmetic on its own; the server-side refusal is what enforces the mode.
_SOURCE_SELECT_RE = re.compile(
    r'[ \t]*<label[^>]*>\s*source\s*<select id="source-select">.*?</select>\s*</label>\s*',
    re.S,
)


def _handler_for(paper_db: str | None, live_db: str | None = None, allow_live: bool = True):
    """Build the request handler. `allow_live=False` makes this server paper-only, refusing the live
    ledger outright rather than merely hiding its selector.

    This exists because the orchestrator embeds this dashboard in an iframe under a card badged
    PAPER, and that badge was a promise this module could not keep: `--source` only ever affected
    `--json`, so the served page always offered both ledgers and a viewer could switch the embedded
    card to real-money data while the surrounding suite dashboard still read PAPER.

    Hiding the dropdown alone would not fix it -- `/api/data?source=live` is a plain GET on a
    loopback port, reachable from the iframe's own console or a stray bookmark. The server has to
    refuse, so the guarantee holds regardless of what reaches the endpoint."""
    live_db = live_db or dbmod.live_db_path()
    html_text = HTML if allow_live else _SOURCE_SELECT_RE.sub("", HTML)
    page = html_text.encode("utf-8")

    class _Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # no-store, not no-cache: the page is baked into this process, so after a restart a cached
            # copy shows a stale layout until a hard refresh; no-store makes the browser always refetch.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's interface
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                self._send(page, "text/html; charset=utf-8")
                return
            if parsed.path == "/api/data":
                query = parse_qs(parsed.query)
                # "live" is opt-in and exact-match only -- anything else (missing, typo'd,
                # empty) falls back to paper, never the other way around. On a paper-only server
                # it is refused outright: this is the half of the guarantee that holds when the
                # request does not come from the page we served.
                source = query.get("source", ["paper"])[0]
                if source == "live" and not allow_live:
                    source = "paper"
                conn = dbmod.connect(live_db if source == "live" else paper_db)
                try:
                    payload = build_api_data(
                        conn,
                        query.get("date", [None])[0],
                        query.get("arm", [None])[0],
                        query.get("symbol", [None])[0],
                    )
                    payload["source"] = source if source == "live" else "paper"
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


def serve(
    port: int,
    db_path: str | None = None,
    open_browser: bool = True,
    live_db: str | None = None,
    allow_live: bool = True,
) -> int:
    if port_in_use(port):
        print(f"already serving on http://{HOST}:{port}")
        if open_browser:
            webbrowser.open(f"http://{HOST}:{port}/")
        return 0
    server = _ThreadingServer((HOST, port), _handler_for(db_path, live_db, allow_live))
    mode = "paper + live source selector" if allow_live else "PAPER ONLY (live ledger refused)"
    print(f"flies dashboard on http://{HOST}:{port}  (loopback only, read-only; {mode})")
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
    ap.add_argument("--db", help="paper DB path override (default: the resolved paper_trades.db)")
    ap.add_argument("--live-db", help="live DB path override (default: the resolved live_trades.db)")
    ap.add_argument(
        "--no-browser",
        action="store_true",
        help="don't open a browser tab on start (for headless/background launches)",
    )
    ap.add_argument("--json", action="store_true", help="print one API payload and exit")
    ap.add_argument(
        "--source",
        choices=["paper", "live"],
        default="paper",
        help="which ledger --json reads (the served dashboard offers both unless --paper-only)",
    )
    ap.add_argument(
        "--paper-only",
        action="store_true",
        help="serve the paper ledger only: drop the source selector and refuse source=live. "
        "Used by the orchestrator's embed, whose card is badged PAPER.",
    )
    args = ap.parse_args(argv)

    if args.json:
        db_path = (args.live_db or dbmod.live_db_path()) if args.source == "live" else args.db
        conn = dbmod.connect(db_path)
        try:
            print(json.dumps(build_api_data(conn), indent=2, default=str))
        finally:
            conn.close()
        return 0
    return serve(
        resolve_port(args.port),
        args.db,
        open_browser=not args.no_browser,
        live_db=args.live_db,
        allow_live=not args.paper_only,
    )


if __name__ == "__main__":
    raise SystemExit(main())
