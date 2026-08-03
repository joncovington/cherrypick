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

async function mountScreenerView(view) {
  const select = view.querySelector("#screener-strategy");
  const tableEl = view.querySelector("#screener-table");
  const skippedEl = view.querySelector("#screener-skipped");
  select.value = view.dataset.strategy || "put_credit_spread";

  async function load() {
    const result = await fetch(`/api/screener?strategy=${select.value}`).then((r) => r.json());
    if (!result.ok) {
      tableEl.textContent = result.error || "screener failed";
      return;
    }
    const rows = result.candidates.map((c) => ({
      symbol: c.symbol,
      spot: c.spot,
      iv_rank: c.iv_rank,
      liquidity: c.liquidity_rating,
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
    const result = await fetch("/api/staged").then((r) => r.json());
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
