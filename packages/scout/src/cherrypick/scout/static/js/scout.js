"use strict";

function csrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.content : "";
}

// Every mutating fetch (Alpine components) and every htmx POST carries the CSRF header the
// SecurityMiddleware requires — attached here once rather than at each call site.
document.body.addEventListener("htmx:configRequest", (evt) => {
  if (evt.detail.verb !== "get") {
    evt.detail.headers["X-Csrf-Token"] = csrfToken();
  }
});

function postJson(path, body) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Csrf-Token": csrfToken() },
    body: JSON.stringify(body),
  }).then((r) => r.json());
}

function watchlistStore() {
  return {
    symbols: [],
    draft: "",
    quotes: {},
    async load() {
      const res = await fetch("/api/watchlist").then((r) => r.json());
      if (res.ok) this.symbols = res.symbols;
    },
    async addFromInput() {
      const sym = this.draft.trim().toUpperCase();
      if (!sym) return;
      const res = await postJson("/api/watchlist", { action: "add", symbols: [sym] });
      if (res.ok) {
        this.symbols = res.symbols;
        this.draft = "";
      }
    },
    async remove(sym) {
      const res = await postJson("/api/watchlist", { action: "remove", symbols: [sym] });
      if (res.ok) this.symbols = res.symbols;
    },
    openSymbol(sym) {
      htmx.ajax("GET", `/partial/symbol/${sym}`, { target: "#content", pushUrl: true });
    },
    openBuilder(sym) {
      htmx.ajax("GET", `/partial/builder/${sym}`, { target: "#content", pushUrl: true });
    },
  };
}

function _firstWatchlistSymbolOrDefault() {
  const store = Alpine.$data(document.getElementById("watchlist"));
  return store.symbols[0] || "SPY";
}

// A plain top-level equivalent of watchlistStore().openBuilder, for callers outside Alpine's scope
// (e.g. a Tabulator cell formatter's click handler in the screener).
function openBuilderFor(sym) {
  htmx.ajax("GET", `/partial/builder/${sym}`, { target: "#content", pushUrl: true });
}

document.getElementById("nav-symbol").addEventListener("click", (evt) => {
  evt.preventDefault();
  const store = Alpine.$data(document.getElementById("watchlist"));
  store.openSymbol(_firstWatchlistSymbolOrDefault());
});

document.getElementById("nav-builder").addEventListener("click", (evt) => {
  evt.preventDefault();
  const store = Alpine.$data(document.getElementById("watchlist"));
  store.openBuilder(_firstWatchlistSymbolOrDefault());
});

/* ---------------- symbol view: candlestick chart + stats panel ---------------- */

let _symbolChart = null;
let _symbolCandleSeries = null;
let _symbolLastBar = null;

function renderStats(el, stats, levels) {
  const pct = (v) => (typeof v === "number" ? `${(v * 100).toFixed(2)}%` : "—");
  const num = (v) => (typeof v === "number" ? v.toFixed(2) : "—");
  const support = levels?.nearest_support;
  const resistance = levels?.nearest_resistance;
  el.innerHTML = `
    <span>Last <b>${num(stats.last_close)}</b></span>
    <span>Chg <b>${pct(stats.change_pct)}</b></span>
    <span>52w ${num(stats.week52_low)}–${num(stats.week52_high)}</span>
    <span>Avg vol (30d) ${stats.avg_volume_30d ? Math.round(stats.avg_volume_30d).toLocaleString() : "—"}</span>
    <span>IV rank ${stats.iv_rank ?? "—"}</span>
    <span>Liquidity ${stats.liquidity_rating ?? "—"}</span>
    <span>Support <b>${support ? num(support.price) : "—"}</b></span>
    <span>Resistance <b>${resistance ? num(resistance.price) : "—"}</b></span>
    ${stats.stale ? '<span class="notice">stale</span>' : ""}
  `;
}

function _trendChip(label) {
  if (!label) return "—";
  const text = label.replace("_", " ");
  const side = label.includes("bullish") ? "up" : label.includes("bearish") ? "down" : "flat";
  return `<span class="trend-chip ${side}">${text}</span>`;
}

function renderAnalysis(el, analysis) {
  if (!analysis || !analysis.ok || (!analysis.price_action && !analysis.headline)) {
    el.innerHTML = "";
    return;
  }
  const headline = analysis.headline
    ? `<p><b>${analysis.headline.scan}:</b> ${analysis.headline.text}</p>`
    : "";
  const bullets = (analysis.bullets || [analysis.price_action]).filter(Boolean);
  el.innerHTML = `
    <p>Trend (scout's own read, provisional): 1M ${_trendChip(analysis.trend_1m)} ·
      6M ${_trendChip(analysis.trend_6m)}</p>
    ${headline}
    <p><b>Price Action:</b></p>
    <ul>${bullets.map((b) => `<li>${b}</li>`).join("")}</ul>
  `;
}

const _SMA_COLORS = { sma20: "#e0b453", sma50: "#b57fd1", sma200: "#7a8794" };

function renderLevelOverlays(chart, candleSeries, levels, lastClose) {
  for (const [name, points] of Object.entries(levels.smas || {})) {
    if (!points.length) continue;
    const line = chart.addSeries(LightweightCharts.LineSeries, {
      color: _SMA_COLORS[name] || "#7a8794",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    line.setData(points);
  }

  // Price lines for the strongest few levels on each side of spot -- every clustered swing at
  // once would wallpaper the chart. Strongest = most touches, nearest price as the tiebreak.
  const byStrength = (side) =>
    (levels.levels || [])
      .filter((lv) => (side === "support" ? lv.price < lastClose : lv.price > lastClose))
      .filter((lv) => lv.kind === side)
      .sort((a, b) => b.touches - a.touches || (side === "support" ? b.price - a.price : a.price - b.price))
      .slice(0, 3);
  for (const lv of byStrength("support")) {
    candleSeries.createPriceLine({
      price: lv.price,
      color: "#7fd1a8",
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      title: `S (${lv.touches})`,
    });
  }
  for (const lv of byStrength("resistance")) {
    candleSeries.createPriceLine({
      price: lv.price,
      color: "#e08b8b",
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      title: `R (${lv.touches})`,
    });
  }
}

async function mountSymbolView(view) {
  const symbol = view.dataset.symbol;
  const chartEl = view.querySelector("#chart-container");
  const statsEl = view.querySelector("#stats-panel");
  if (!chartEl) return;

  chartEl.innerHTML = '<p class="loading">Loading candles…</p>';
  if (statsEl) statsEl.innerHTML = '<span class="loading">Loading…</span>';

  let candles, stats, levels, analysis;
  try {
    [candles, stats, levels, analysis] = await Promise.all([
      fetch(`/api/symbol/${symbol}/candles`).then((r) => r.json()),
      fetch(`/api/symbol/${symbol}/stats`).then((r) => r.json()),
      fetch(`/api/symbol/${symbol}/levels`).then((r) => r.json()),
      fetch(`/api/symbol/${symbol}/analysis`).then((r) => r.json()),
    ]);
  } catch {
    chartEl.textContent = "Could not reach the scout server -- is it still running?";
    return;
  }
  if (statsEl) renderStats(statsEl, stats, levels);
  const analysisEl = view.querySelector("#analysis-panel");
  if (analysisEl) renderAnalysis(analysisEl, analysis);

  if (_symbolChart) {
    _symbolChart.remove();
    _symbolChart = null;
    _symbolCandleSeries = null;
    _symbolLastBar = null;
  }
  if (!candles.ok || !candles.bars.length) {
    chartEl.textContent = "No candle data available.";
    return;
  }

  const chart = LightweightCharts.createChart(chartEl, {
    layout: { background: { color: "#181e24" }, textColor: "#d7dee5" },
    grid: { vertLines: { color: "#232c34" }, horzLines: { color: "#232c34" } },
    height: 420,
  });
  _symbolChart = chart;

  const candleSeries = chart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor: "#4d9de0",
    downColor: "#e08b8b",
    borderVisible: false,
    wickUpColor: "#4d9de0",
    wickDownColor: "#e08b8b",
  });
  candleSeries.setData(
    candles.bars.map((b) => ({ time: b.t, open: b.o, high: b.h, low: b.l, close: b.c }))
  );
  _symbolCandleSeries = candleSeries;
  _symbolLastBar = { ...candles.bars[candles.bars.length - 1] };

  const volumeSeries = chart.addSeries(LightweightCharts.HistogramSeries, {
    priceFormat: { type: "volume" },
    priceScaleId: "",
  });
  volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
  volumeSeries.setData(
    candles.bars.filter((b) => b.v != null).map((b) => ({ time: b.t, value: b.v }))
  );

  if (levels && levels.ok) {
    renderLevelOverlays(chart, candleSeries, levels, candles.bars[candles.bars.length - 1].c);
  }

  chart.timeScale().fitContent();
}

/* ---------------- screener: ranked candidate table ---------------- */

let _screenerTable = null;

function _fmtPct(v) {
  return typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "—";
}
function _fmtNum(v) {
  return typeof v === "number" ? v.toFixed(2) : "—";
}

function _chipFilterParams(view) {
  // Selected chips per group -> comma-separated query params. An empty group sends nothing, which
  // the API treats as "apply the config default gate for that dimension".
  const params = new URLSearchParams();
  view.querySelectorAll(".chip-group").forEach((group) => {
    const selected = [...group.querySelectorAll(".chip.on")].map((c) => c.dataset.bucket);
    if (selected.length) params.set(group.dataset.filter, selected.join(","));
  });
  return params;
}

function _fmtCap(v) {
  if (typeof v !== "number") return "—";
  if (v >= 1e12) return `${(v / 1e12).toFixed(1)}T`;
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  return `${(v / 1e6).toFixed(0)}M`;
}

async function mountScreenerView(view) {
  const select = view.querySelector("#screener-strategy");
  const tableEl = view.querySelector("#screener-table");
  const skippedEl = view.querySelector("#screener-skipped");
  select.value = view.dataset.strategy || "put_credit_spread";

  async function load() {
    // A fresh cache can take a while to warm (cold candle/chain/quote fetches); an existing table
    // stays visible during a refresh rather than being wiped, so only the first-ever load shows this.
    if (!_screenerTable) tableEl.innerHTML = '<p class="loading">Scanning the watchlist…</p>';
    const params = _chipFilterParams(view);
    params.set("strategy", select.value);
    let result;
    try {
      result = await fetch(`/api/screener?${params.toString()}`).then((r) => r.json());
    } catch {
      tableEl.textContent = "Could not reach the scout server -- is it still running?";
      return;
    }
    if (!result.ok) {
      tableEl.textContent = result.error || "screener failed";
      return;
    }
    const rows = result.candidates.map((c) => ({
      symbol: c.symbol,
      spot: c.spot,
      iv_rank: c.iv_rank,
      liquidity: c.liquidity_rating,
      market_cap: c.market_cap,
      skew_edge: c.skew_edge,
      strikes: c.legs.map((l) => l.strike).join(" / "),
      dte: c.dte,
      credit: c.credit,
      max_risk: c.max_risk,
      pop: c.pop,
      pop_heuristic: c.pop_heuristic,
      breakevens: (c.breakevens || []).map((b) => b.toFixed(2)).join(", "),
      return_on_risk: c.return_on_risk,
      composite_score: c.composite_score,
    }));

    if (_screenerTable) {
      _screenerTable.setData(rows);
    } else {
      _screenerTable = new Tabulator(tableEl, {
        data: rows,
        layout: "fitColumns",
        persistence: true,
        persistenceID: "scout-screener",
        placeholder: "No candidates matched -- try a different strategy or widen the watchlist.",
        initialSort: [{ column: "composite_score", dir: "desc" }],
        columns: [
          {
            title: "Symbol",
            field: "symbol",
            formatter: (cell) =>
              `<a href="#" data-sym="${cell.getValue()}" class="builder-link">${cell.getValue()}</a>`,
            cellClick: (_e, cell) => openBuilderFor(cell.getValue()),
          },
          { title: "Spot", field: "spot", formatter: (c) => _fmtNum(c.getValue()) },
          { title: "IV rank", field: "iv_rank", formatter: (c) => _fmtPct(c.getValue()) },
          { title: "Liquidity", field: "liquidity" },
          { title: "Mkt cap", field: "market_cap", formatter: (c) => _fmtCap(c.getValue()) },
          { title: "Skew edge", field: "skew_edge", formatter: (c) => _fmtNum(c.getValue()) },
          { title: "Strikes", field: "strikes" },
          { title: "DTE", field: "dte" },
          { title: "Credit", field: "credit", formatter: (c) => _fmtNum(c.getValue()) },
          { title: "Max risk", field: "max_risk", formatter: (c) => _fmtNum(c.getValue()) },
          { title: "POP (model)", field: "pop", formatter: (c) => _fmtPct(c.getValue()) },
          { title: "POP (1-2d)", field: "pop_heuristic", formatter: (c) => _fmtPct(c.getValue()) },
          { title: "Breakevens", field: "breakevens" },
          {
            title: "Return/risk",
            field: "return_on_risk",
            formatter: (c) => _fmtPct(c.getValue()),
          },
          { title: "Score", field: "composite_score", formatter: (c) => _fmtNum(c.getValue()) },
        ],
      });
    }
    skippedEl.textContent = result.skipped.length
      ? `Skipped: ${result.skipped.map((s) => `${s.symbol} (${s.reason})`).join("; ")}`
      : "";
  }

  select.onchange = load;
  view.querySelectorAll(".chip").forEach((chip) => {
    chip.onclick = () => {
      chip.classList.toggle("on");
      load();
    };
  });
  await load();
}

/* ---------------- staged tickets: list, copy, delete ---------------- */

function _fmtDateTime(ts) {
  return ts ? new Date(ts * 1000).toLocaleString() : "—";
}

function _ticketDryRunSummary(dryRun) {
  if (!dryRun) return "not validated";
  if (!dryRun.ok) return `validation failed: ${dryRun.error || "unknown error"}`;
  const bp = dryRun.buying_power || {};
  return `account ${dryRun.account_number || "—"} · BP change ${bp.change_in_buying_power ?? "—"}`;
}

function _ticketLegLine(leg) {
  return `${leg.quantity > 0 ? "Buy" : "Sell"} ${Math.abs(leg.quantity)} ${leg.symbol} @ ${leg.price.toFixed(2)}`;
}

function _ticketDescription(ticket) {
  const lines = [`${ticket.symbol} ${ticket.strategy}`, ...ticket.legs.map(_ticketLegLine)];
  if (ticket.credit != null) lines.push(`Net credit: ${ticket.credit.toFixed(2)}`);
  if (ticket.note) lines.push(`Note: ${ticket.note}`);
  return lines.join("\n");
}

async function mountStagedView(view) {
  const listEl = view.querySelector("#staged-list");

  async function load() {
    listEl.innerHTML = '<p class="loading">Loading staged tickets…</p>';
    let result;
    try {
      result = await fetch("/api/staged").then((r) => r.json());
    } catch {
      listEl.innerHTML = '<p class="notice">Could not reach the scout server -- is it still running?</p>';
      return;
    }
    const tickets = result.tickets || [];
    if (!tickets.length) {
      listEl.innerHTML = '<p class="note">No staged tickets.</p>';
      return;
    }
    listEl.innerHTML = tickets
      .map(
        (t) => `
      <div class="staged-ticket" data-id="${t.id}">
        <div class="staged-head">
          <b>${t.symbol}</b> <span class="note">${t.strategy}</span>
          <span class="note">${_fmtDateTime(t.created_at)}</span>
        </div>
        <div class="staged-legs">${t.legs.map(_ticketLegLine).join("<br>")}</div>
        <div class="note">Credit ${t.credit != null ? t.credit.toFixed(2) : "—"} ·
          Max risk ${t.max_risk != null ? t.max_risk.toFixed(2) : "—"}</div>
        <div class="note">${_ticketDryRunSummary(t.dry_run)}</div>
        <div class="staged-actions">
          <button data-act="copy">Copy</button>
          <button data-act="delete">Delete</button>
        </div>
      </div>`
      )
      .join("");
    listEl.querySelectorAll(".staged-ticket").forEach((row) => {
      const id = row.dataset.id;
      const ticket = tickets.find((t) => t.id === id);
      row.querySelector('[data-act="copy"]').onclick = () => {
        navigator.clipboard?.writeText(_ticketDescription(ticket));
      };
      row.querySelector('[data-act="delete"]').onclick = async () => {
        await postJson("/api/staged/delete", { id });
        load();
      };
    });
  }

  await load();
}

document.body.addEventListener("htmx:afterSwap", (evt) => {
  const symbolView = evt.detail.target.querySelector("#symbol-view");
  if (symbolView) mountSymbolView(symbolView);
  const screenerView = evt.detail.target.querySelector("#screener-view");
  if (screenerView) mountScreenerView(screenerView);
  const stagedView = evt.detail.target.querySelector("#staged-view");
  if (stagedView) mountStagedView(stagedView);
});

/* ---------------- live quotes: one SSE connection per session ---------------- */

function applyQuotes(changed) {
  const watchlistEl = document.getElementById("watchlist");
  if (watchlistEl) {
    const store = Alpine.$data(watchlistEl);
    store.quotes = { ...store.quotes, ...changed };
  }

  const view = document.getElementById("symbol-view");
  if (view && _symbolCandleSeries && _symbolLastBar) {
    const quote = changed[view.dataset.symbol];
    if (quote && quote.last != null) {
      _symbolLastBar = {
        ..._symbolLastBar,
        c: quote.last,
        h: Math.max(_symbolLastBar.h, quote.last),
        l: Math.min(_symbolLastBar.l, quote.last),
      };
      _symbolCandleSeries.update({
        time: _symbolLastBar.t,
        open: _symbolLastBar.o,
        high: _symbolLastBar.h,
        low: _symbolLastBar.l,
        close: _symbolLastBar.c,
      });
    }
  }
}

let _quoteStream = null;

function initQuoteStream() {
  if (_quoteStream) return;
  _quoteStream = new EventSource("/api/stream");
  _quoteStream.addEventListener("quotes", (evt) => {
    const payload = JSON.parse(evt.data);
    applyQuotes(payload.symbols || {});
  });
}

initQuoteStream();
