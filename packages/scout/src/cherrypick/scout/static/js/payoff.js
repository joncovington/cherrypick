"use strict";

/* ---------------- the leg-basket builder: chain picker + SVG payoff diagram ---------------- */

function _builderState() {
  return { symbol: null, expiration: null, legs: [], spot: null, iv: null, lastResult: null };
}

let _builder = _builderState();

function _daysToExpiration(expirationIso) {
  const ms = new Date(expirationIso + "T00:00:00Z") - new Date();
  return Math.max(0, ms / 86400000);
}

async function mountBuilderView(view) {
  _builder = _builderState();
  _builder.symbol = view.dataset.symbol;

  const expSelect = view.querySelector("#builder-expiration");
  const spotInput = view.querySelector("#builder-spot");
  const ivInput = view.querySelector("#builder-iv");

  view.querySelector("#builder-chain").innerHTML = '<p class="loading">Loading option chain…</p>';

  let quote, expirations;
  try {
    // /quote, not /stats -- the builder only needs spot + IV, and /stats is candle-history-backed
    // (week52 range, avg volume) which means a cold DXLink backfill on every symbol selection.
    [quote, expirations] = await Promise.all([
      fetch(`/api/symbol/${_builder.symbol}/quote`).then((r) => r.json()),
      fetch(`/api/symbol/${_builder.symbol}/expirations`).then((r) => r.json()),
    ]);
  } catch {
    view.querySelector("#builder-chain").textContent =
      "Could not reach the scout server -- is it still running?";
    return;
  }
  _builder.spot = quote.last ?? 100;
  _builder.iv = quote.iv_30d ?? 0.3;
  spotInput.value = _builder.spot;
  ivInput.value = _builder.iv;

  const dates = Object.keys(expirations.expirations || {}).sort();
  expSelect.innerHTML = dates.map((d) => `<option value="${d}">${d}</option>`).join("");
  if (dates.length) {
    _builder.expiration = dates[0];
    await loadChain(view);
  } else {
    view.querySelector("#builder-chain").textContent = "No option chain available for this symbol.";
  }

  expSelect.onchange = () => {
    _builder.expiration = expSelect.value;
    loadChain(view);
  };
  spotInput.oninput = () => {
    _builder.spot = parseFloat(spotInput.value) || _builder.spot;
    computePayoff(view);
  };
  ivInput.oninput = () => {
    _builder.iv = parseFloat(ivInput.value) || _builder.iv;
    computePayoff(view);
  };
  view.querySelector("#builder-validate").onclick = () => validateOrder(view);
  view.querySelector("#builder-stage").onclick = () => stageTicket(view);

  renderLegs(view);
  computePayoff(view);
  loadIncomeGrid(view); // fire-and-forget; the grid arrives when greeks do
}

const _TIER_LABELS = { conservative: "Conservative", optimal: "Optimal", aggressive: "Aggressive" };
const _BUCKET_LABELS = { short: "20–39d", medium: "40–70d", long: "71–180d" };

async function loadIncomeGrid(view) {
  const el = view.querySelector("#income-grid");
  if (!el || !_builder.spot) return;
  el.innerHTML = '<p class="loading">Loading income grid…</p>';
  let res;
  try {
    res = await fetch(
      `/api/symbol/${_builder.symbol}/income-grid?spot=${_builder.spot}&kind=put`
    ).then((r) => r.json());
  } catch {
    el.innerHTML = "";
    return;
  }
  const buckets = ["short", "medium", "long"].filter((b) => res.grid && res.grid[b]);
  if (!buckets.length) {
    el.innerHTML = "";
    return;
  }
  const pct = (v) => (typeof v === "number" ? `${(v * 100).toFixed(0)}%` : "--");
  let html = `<p><b>Short put grid</b> <span class="note">strikes at ~15/25/35 delta (live greeks); click to load as a leg</span></p>`;
  html += `<table class="income-grid"><thead><tr><th></th>${buckets
    .map((b) => `<th>${_BUCKET_LABELS[b]}<br><span class="note">${res.grid[b].expiration}</span></th>`)
    .join("")}</tr></thead><tbody>`;
  for (const tier of ["conservative", "optimal", "aggressive"]) {
    html += `<tr><td class="chip-label">${_TIER_LABELS[tier]}</td>`;
    for (const b of buckets) {
      const cell = res.grid[b].tiers[tier];
      if (!cell) {
        html += "<td>--</td>";
        continue;
      }
      html += `<td><button type="button" class="grid-cell" data-cell='${JSON.stringify(cell)}'>
        ${cell.strike}p <span class="note">Δ${cell.delta.toFixed(2)}</span><br>
        <span class="note">POW ${pct(cell.pow)} · ${pct(cell.annualized_return)}/yr*</span>
      </button></td>`;
    }
    html += "</tr>";
  }
  html += "</tbody></table>";
  el.innerHTML = html;
  el.querySelectorAll(".grid-cell").forEach((btn) => {
    btn.onclick = () => {
      const cell = JSON.parse(btn.dataset.cell);
      _builder.legs = [
        {
          kind: "put",
          strike: cell.strike,
          quantity: -1,
          price: cell.mid || 0,
          symbol: cell.symbol,
          expiration: cell.expiration,
        },
      ];
      _builder.expiration = cell.expiration;
      renderLegs(view);
      computePayoff(view);
      loadWarnings(view);
    };
  });
}

async function loadWarnings(view) {
  const el = view.querySelector("#builder-warnings");
  if (!el || !_builder.expiration) return;
  try {
    const res = await fetch(
      `/api/symbol/${_builder.symbol}/warnings?expiration=${encodeURIComponent(_builder.expiration)}`
    ).then((r) => r.json());
    el.innerHTML = (res.warnings || []).map((w) => `<p class="notice">${w}</p>`).join("");
  } catch {
    el.innerHTML = ""; // warnings are best-effort; their absence must not block the builder
  }
}

async function loadChain(view) {
  const chainEl = view.querySelector("#builder-chain");
  const expSelect = view.querySelector("#builder-expiration");
  expSelect.value = _builder.expiration;
  loadWarnings(view); // fire-and-forget alongside the chain fetch
  chainEl.innerHTML = '<p class="loading">Loading option chain…</p>';
  let data;
  try {
    data = await fetch(
      `/api/symbol/${_builder.symbol}/chain?expiration=${encodeURIComponent(_builder.expiration)}`
    ).then((r) => r.json());
  } catch {
    chainEl.textContent = "Could not reach the scout server -- is it still running?";
    return;
  }
  if (!data.options || !data.options.length) {
    chainEl.textContent = "No strikes available for this expiration.";
    return;
  }

  const byStrike = {};
  _builder.chainGreeks = {};
  for (const opt of data.options || []) {
    byStrike[opt.strike] ??= {};
    byStrike[opt.strike][opt.option_type] = opt;
    if (opt.greeks) _builder.chainGreeks[opt.symbol] = opt.greeks;
  }
  const strikes = Object.keys(byStrike)
    .map(Number)
    .sort((a, b) => a - b);

  const rows = strikes.map((strike) => {
    const call = byStrike[strike].C;
    const put = byStrike[strike].P;
    const mid = (opt) => (opt && opt.quote && opt.quote.mid != null ? opt.quote.mid.toFixed(2) : "--");
    return `<tr>
      <td>${call ? mid(call) : "--"}</td>
      <td>${call ? `<button data-symbol="${call.symbol}" data-strike="${strike}" data-kind="call" data-price="${call.quote?.mid ?? 0}" data-dir="1">Buy</button>
                    <button data-symbol="${call.symbol}" data-strike="${strike}" data-kind="call" data-price="${call.quote?.mid ?? 0}" data-dir="-1">Sell</button>` : ""}</td>
      <td class="builder-strike">${strike}</td>
      <td>${put ? `<button data-symbol="${put.symbol}" data-strike="${strike}" data-kind="put" data-price="${put.quote?.mid ?? 0}" data-dir="1">Buy</button>
                   <button data-symbol="${put.symbol}" data-strike="${strike}" data-kind="put" data-price="${put.quote?.mid ?? 0}" data-dir="-1">Sell</button>` : ""}</td>
      <td>${put ? mid(put) : "--"}</td>
    </tr>`;
  });
  chainEl.innerHTML = `<table><thead><tr><th>Call mid</th><th></th><th>Strike</th><th></th><th>Put mid</th></tr></thead>
    <tbody>${rows.join("")}</tbody></table>`;
  chainEl.querySelectorAll("button[data-symbol]").forEach((btn) => {
    btn.onclick = () => addLeg(view, btn.dataset);
  });
}

function addLeg(view, data) {
  const greeks = (_builder.chainGreeks || {})[data.symbol] || {};
  _builder.legs.push({
    kind: data.kind,
    strike: parseFloat(data.strike),
    quantity: parseInt(data.dir, 10),
    price: parseFloat(data.price) || 0,
    symbol: data.symbol,
    expiration: _builder.expiration,
    delta: greeks.delta ?? null,
    gamma: greeks.gamma ?? null,
    theta: greeks.theta ?? null,
    vega: greeks.vega ?? null,
  });
  renderLegs(view);
  computePayoff(view);
}

function renderLegs(view) {
  const el = view.querySelector("#builder-legs");
  el.innerHTML = _builder.legs
    .map(
      (leg, i) =>
        `<li>${leg.quantity > 0 ? "Buy" : "Sell"} ${leg.kind} ${leg.strike} @ ${leg.price.toFixed(2)}
          <button data-i="${i}" title="remove">&times;</button></li>`
    )
    .join("");
  el.querySelectorAll("button[data-i]").forEach((btn) => {
    btn.onclick = () => {
      _builder.legs.splice(parseInt(btn.dataset.i, 10), 1);
      renderLegs(view);
      computePayoff(view);
    };
  });
}

async function computePayoff(view) {
  const svg = view.querySelector("#builder-svg");
  const metricsEl = view.querySelector("#builder-metrics");
  if (!_builder.legs.length) {
    svg.innerHTML = "";
    metricsEl.innerHTML = '<span class="note">Add a leg to see the payoff.</span>';
    return;
  }
  const dte = _builder.expiration ? _daysToExpiration(_builder.expiration) : null;
  const params = new URLSearchParams({
    legs: JSON.stringify(_builder.legs),
    spot: String(_builder.spot),
  });
  if (dte != null) params.set("dte", String(dte));
  if (_builder.iv != null) params.set("iv", String(_builder.iv));
  if (_builder.symbol) params.set("symbol", _builder.symbol);
  if (_builder.expiration) params.set("expiration", _builder.expiration);

  let result;
  try {
    result = await fetch(`/api/payoff?${params.toString()}`).then((r) => r.json());
  } catch {
    svg.innerHTML = "";
    metricsEl.innerHTML = '<span class="notice">Could not reach the scout server -- is it still running?</span>';
    return;
  }
  _builder.lastResult = result;
  renderPayoffSvg(svg, result, _builder.spot);
  renderMetrics(metricsEl, result);
}

/* ---------------- order staging: validate (dry-run) and stage a leg basket ---------------- */

function _orderLegs() {
  return _builder.legs.map((leg) => ({ symbol: leg.symbol, quantity: leg.quantity, price: leg.price }));
}

function _netCredit() {
  // Total dollar credit (positive) or debit (negative) across the basket at 1x -- the same *100
  // convention the screener's `credit` column uses, not the per-share order price.
  return -_builder.legs.reduce((sum, leg) => sum + leg.quantity * leg.price, 0) * 100;
}

function renderDryRunSummary(result) {
  if (!result) return '<span class="note">not validated</span>';
  if (!result.ok) {
    const problems = (result.problems || []).join("; ");
    return `<span class="notice">${result.error || "validation failed"}${problems ? ": " + problems : ""}</span>`;
  }
  const bp = result.buying_power || {};
  const warnCount = (bp.warnings || []).length;
  return `<span>Account ${result.account_number || "—"} · BP change ${bp.change_in_buying_power ?? "—"}${
    warnCount ? ` · ${warnCount} warning(s)` : ""
  }</span>`;
}

async function validateOrder(view) {
  const el = view.querySelector("#builder-validation");
  if (!_builder.legs.length) {
    el.textContent = "Add a leg first.";
    return;
  }
  el.textContent = "Validating…";
  try {
    const result = await postJson("/api/order/dry-run", { legs: _orderLegs() });
    el.innerHTML = renderDryRunSummary(result);
  } catch {
    el.innerHTML = '<span class="notice">Could not reach the scout server -- is it still running?</span>';
  }
}

async function stageTicket(view) {
  const el = view.querySelector("#builder-validation");
  if (!_builder.legs.length) {
    el.textContent = "Add a leg first.";
    return;
  }
  const note = view.querySelector("#builder-note").value.trim() || null;
  const result = _builder.lastResult;
  const maxRisk =
    result && result.max_loss && !result.max_loss.unbounded ? Math.abs(result.max_loss.value) : null;
  el.textContent = "Staging…";
  try {
    const res = await postJson("/api/staged", {
      symbol: _builder.symbol,
      strategy: "custom",
      legs: _orderLegs(),
      credit: _netCredit(),
      max_risk: maxRisk,
      note,
    });
    el.innerHTML = res.ok
      ? `Staged. ${renderDryRunSummary(res.ticket.dry_run)}`
      : `<span class="notice">${res.error || "stage failed"}</span>`;
  } catch {
    el.innerHTML = '<span class="notice">Could not reach the scout server -- is it still running?</span>';
  }
}

function renderMetrics(el, result) {
  const fmt = (v) => (typeof v === "number" ? v.toFixed(2) : "--");
  const pct = (v) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "--");
  // Live DXLink greeks (attached per leg from the chain) beat model greeks when present.
  const live = result.net_greeks && result.net_greeks.delta != null;
  const greeks = (live ? result.net_greeks : result.model_greeks) || {};
  const greeksTag = live ? "Live" : "Model";
  el.innerHTML = `
    <span>Max profit ${result.max_profit.unbounded ? "unbounded" : fmt(result.max_profit.value)}</span>
    <span>Max loss ${result.max_loss.unbounded ? "unbounded" : fmt(result.max_loss.value)}</span>
    <span>Breakevens ${result.breakevens.map(fmt).join(", ") || "--"}</span>
    <span>POP ${pct(result.pop)} · POW ${pct(result.pow)}</span>
    <span>Return ${pct(result.raw_return)} raw · ${pct(result.annualized_return)} annualized*</span>
    <span>${greeksTag} Δ ${fmt(greeks.delta)} · Θ ${fmt(greeks.theta)} · Vega ${fmt(greeks.vega)}</span>
  `;
  const cardEl = document.getElementById("builder-strategy-card");
  if (cardEl) {
    const parts = [];
    if (result.explanation) parts.push(`<p><b>Strategy:</b> ${result.explanation}</p>`);
    if (result.suggestion) parts.push(`<p>${result.suggestion}</p>`);
    if (result.greeks_text) parts.push(`<p class="note">${result.greeks_text}</p>`);
    if (result.annualized_return != null) {
      parts.push(
        '<p class="note">* Annualized assumes the same return could be repeated back-to-back all year -- a comparison metric, not a forecast.</p>'
      );
    }
    cardEl.innerHTML = parts.join("");
  }
}

function renderPayoffSvg(svg, result, spot) {
  const curve = result.curve;
  if (!curve.length) {
    svg.innerHTML = "";
    return;
  }
  const W = 640,
    H = 280,
    PAD = 30;
  const spots = curve.map((p) => p.spot).concat([spot]);
  const pnls = curve.map((p) => p.pnl).concat([0]);
  const xMin = Math.min(...spots),
    xMax = Math.max(...spots);
  const yAbsMax = Math.max(1, ...pnls.map(Math.abs));
  const xScale = (x) => PAD + ((x - xMin) / (xMax - xMin || 1)) * (W - 2 * PAD);
  const yScale = (y) => H / 2 - (y / yAbsMax) * (H / 2 - PAD);

  const points = curve.map((p) => `${xScale(p.spot)},${yScale(p.pnl)}`).join(" ");
  const zeroY = yScale(0);
  const spotX = xScale(spot);

  svg.innerHTML = `
    <line x1="0" y1="${zeroY}" x2="${W}" y2="${zeroY}" stroke="#3a4652" stroke-width="1"/>
    <line x1="${spotX}" y1="0" x2="${spotX}" y2="${H}" stroke="#4d9de0" stroke-width="1" stroke-dasharray="4,3"/>
    <polyline points="${points}" fill="none" stroke="#7fd1a8" stroke-width="2"/>
  `;
}

document.body.addEventListener("htmx:afterSwap", (evt) => {
  const view = evt.detail.target.querySelector("#builder-view");
  if (view) mountBuilderView(view);
});
