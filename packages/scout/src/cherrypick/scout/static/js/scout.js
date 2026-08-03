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

function renderStats(el, stats) {
  const pct = (v) => (typeof v === "number" ? `${(v * 100).toFixed(2)}%` : "—");
  const num = (v) => (typeof v === "number" ? v.toFixed(2) : "—");
  el.innerHTML = `
    <span>Last <b>${num(stats.last_close)}</b></span>
    <span>Chg <b>${pct(stats.change_pct)}</b></span>
    <span>52w ${num(stats.week52_low)}–${num(stats.week52_high)}</span>
    <span>Avg vol (30d) ${stats.avg_volume_30d ? Math.round(stats.avg_volume_30d).toLocaleString() : "—"}</span>
    <span>IV rank ${stats.iv_rank ?? "—"}</span>
    <span>Liquidity ${stats.liquidity_rating ?? "—"}</span>
    ${stats.stale ? '<span class="notice">stale</span>' : ""}
  `;
}

async function mountSymbolView(view) {
  const symbol = view.dataset.symbol;
  const chartEl = view.querySelector("#chart-container");
  const statsEl = view.querySelector("#stats-panel");
  if (!chartEl) return;

  const [candles, stats] = await Promise.all([
    fetch(`/api/symbol/${symbol}/candles`).then((r) => r.json()),
    fetch(`/api/symbol/${symbol}/stats`).then((r) => r.json()),
  ]);
  if (statsEl) renderStats(statsEl, stats);

  if (_symbolChart) {
    _symbolChart.remove();
    _symbolChart = null;
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

  const volumeSeries = chart.addSeries(LightweightCharts.HistogramSeries, {
    priceFormat: { type: "volume" },
    priceScaleId: "",
  });
  volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
  volumeSeries.setData(
    candles.bars.filter((b) => b.v != null).map((b) => ({ time: b.t, value: b.v }))
  );

  chart.timeScale().fitContent();
}

document.body.addEventListener("htmx:afterSwap", (evt) => {
  const view = evt.detail.target.querySelector("#symbol-view");
  if (view) mountSymbolView(view);
});
