"use strict";

/* ---------------- the leg-basket builder: chain picker + SVG payoff diagram ---------------- */

function _builderState() {
  return { symbol: null, expiration: null, legs: [], spot: null, iv: null };
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

  const stats = await fetch(`/api/symbol/${_builder.symbol}/stats`).then((r) => r.json());
  _builder.spot = stats.last_close ?? 100;
  _builder.iv = stats.iv_30d ?? 0.3;
  spotInput.value = _builder.spot;
  ivInput.value = _builder.iv;

  const expirations = await fetch(`/api/symbol/${_builder.symbol}/expirations`).then((r) => r.json());
  const dates = Object.keys(expirations.expirations || {}).sort();
  expSelect.innerHTML = dates.map((d) => `<option value="${d}">${d}</option>`).join("");
  if (dates.length) {
    _builder.expiration = dates[0];
    await loadChain(view);
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

  renderLegs(view);
  computePayoff(view);
}

async function loadChain(view) {
  const chainEl = view.querySelector("#builder-chain");
  const expSelect = view.querySelector("#builder-expiration");
  expSelect.value = _builder.expiration;
  const data = await fetch(
    `/api/symbol/${_builder.symbol}/chain?expiration=${encodeURIComponent(_builder.expiration)}`
  ).then((r) => r.json());

  const byStrike = {};
  for (const opt of data.options || []) {
    byStrike[opt.strike] ??= {};
    byStrike[opt.strike][opt.option_type] = opt;
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
  _builder.legs.push({
    kind: data.kind,
    strike: parseFloat(data.strike),
    quantity: parseInt(data.dir, 10),
    price: parseFloat(data.price) || 0,
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

  const result = await fetch(`/api/payoff?${params.toString()}`).then((r) => r.json());
  renderPayoffSvg(svg, result, _builder.spot);
  renderMetrics(metricsEl, result);
}

function renderMetrics(el, result) {
  const fmt = (v) => (typeof v === "number" ? v.toFixed(2) : "--");
  const pct = (v) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "--");
  el.innerHTML = `
    <span>Max profit ${result.max_profit.unbounded ? "unbounded" : fmt(result.max_profit.value)}</span>
    <span>Max loss ${result.max_loss.unbounded ? "unbounded" : fmt(result.max_loss.value)}</span>
    <span>Breakevens ${result.breakevens.map(fmt).join(", ") || "--"}</span>
    <span>POP ${pct(result.pop)}</span>
    <span>Net delta ${fmt(result.net_greeks.delta)}</span>
  `;
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
