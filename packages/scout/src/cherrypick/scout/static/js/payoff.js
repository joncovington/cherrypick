"use strict";

/* ---------------- the leg-basket builder: chain picker + SVG payoff diagram ---------------- */

function _builderState() {
  return {
    symbol: null,
    expiration: null,
    legs: [],
    spot: null,
    iv: null,
    lastResult: null,
    priceBy: "mid",
    chainGreeks: {},
    chainQuotes: {},
    chainByKey: {},
    strikes: [],
  };
}

function _legKey(kind, strike) {
  return `${kind}:${strike}`;
}

// Fill price under the current price-by mode: mid, or the "natural" side (sell at bid, buy at ask).
function _fillPrice(leg) {
  const quote = _builder.chainQuotes[leg.symbol];
  if (!quote) return leg.price;
  if (_builder.priceBy === "mid") return quote.mid ?? leg.price;
  return (leg.quantity < 0 ? quote.bid : quote.ask) ?? leg.price;
}

function _reprice(leg) {
  if (!leg.manualPrice && leg.kind !== "stock") leg.price = _fillPrice(leg) ?? leg.price;
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
  spotInput.value = _builder.spot.toFixed(2);
  // Spot/IVR are read-only, sourced live -- IVR (IV Rank) is a 0..100 percentile, shown in the
  // "NN/100" form the reference platform's own info bar uses, not the raw IV decimal
  // (_builder.iv, still used internally for POP/expected-move/template calcs -- a different
  // number that just happens to share the "IV" name).
  const ivRank = quote.iv_rank != null ? Math.round(parseFloat(quote.iv_rank) * 100) : null;
  ivInput.value = ivRank != null ? `${ivRank}/100` : "--";

  const dates = Object.keys(expirations.expirations || {}).sort();
  expSelect.innerHTML = dates.map((d) => `<option value="${d}">${d}</option>`).join("");
  if (dates.length) {
    // Same default the sentiment suggestion cards use (next standard monthly >= 30 DTE, else
    // nearest >= 30 DTE, else farthest listed) -- the API computes it once so both agree.
    _builder.expiration =
      expirations.default_expiration && dates.includes(expirations.default_expiration)
        ? expirations.default_expiration
        : dates[0];
    expSelect.value = _builder.expiration;
    await loadChain(view);
  } else {
    view.querySelector("#builder-chain").textContent = "No option chain available for this symbol.";
  }

  expSelect.onchange = () => {
    _builder.expiration = expSelect.value;
    loadChain(view);
  };
  view.querySelector("#builder-validate").onclick = () => validateOrder(view);
  view.querySelector("#builder-stage").onclick = () => stageTicket(view);

  const chainToggle = view.querySelector("#builder-chain-toggle");
  const chainEl = view.querySelector("#builder-chain");
  chainToggle.onclick = () => {
    const collapsed = chainEl.classList.toggle("collapsed");
    chainToggle.innerHTML = `${collapsed ? "&#9656;" : "&#9662;"} Option chain`;
    if (!collapsed) chainEl.querySelector("tr.builder-atm-row")?.scrollIntoView({ block: "center" });
  };

  const templateSel = view.querySelector("#template-select");
  templateSel.onchange = async () => {
    if (!templateSel.value) return;
    const params = { action: "build", name: templateSel.value };
    if (_builder.iv) params.iv = String(_builder.iv);
    const dte = _builder.expiration ? _daysToExpiration(_builder.expiration) : null;
    if (dte) params.template_dte = String(dte);
    const legs = await _templateCall(view, params);
    if (legs) _setLegs(view, legs);
  };
  view.querySelector("#price-by").onchange = (e) => {
    _builder.priceBy = e.target.value;
    _builder.legs.forEach((leg) => {
      leg.manualPrice = false;
      _reprice(leg);
    });
    renderLegs(view);
    computePayoff(view);
  };
  view.querySelector("#flip-strategy").onclick = async () => {
    if (!_builder.legs.length) return;
    const legs = await _templateCall(view, { action: "flip", legs: JSON.stringify(_builder.legs) });
    if (legs) {
      templateSel.value = "";
      _setLegs(view, legs);
    }
  };
  const width = async (step) => {
    if (!_builder.legs.length) return;
    const legs = await _templateCall(view, {
      action: "width",
      step: String(step),
      legs: JSON.stringify(_builder.legs),
    });
    if (legs) _setLegs(view, legs);
  };
  view.querySelector("#width-widen").onclick = () => width(1);
  view.querySelector("#width-narrow").onclick = () => width(-1);
  view.querySelector("#reset-legs").onclick = () => {
    templateSel.value = "";
    _setLegs(view, []);
  };
  const addOption = (kind) => {
    if (!_builder.strikes.length) return;
    const atm = _builder.strikes.reduce((a, b) =>
      Math.abs(b - _builder.spot) < Math.abs(a - _builder.spot) ? b : a
    );
    const leg = { kind, strike: atm, quantity: 1, price: 0 };
    _applyChainOption(leg, kind, atm);
    _builder.legs.push(leg);
    renderLegs(view);
    computePayoff(view);
  };
  view.querySelector("#add-call").onclick = () => addOption("call");
  view.querySelector("#add-put").onclick = () => addOption("put");
  view.querySelectorAll(".chip.sentiment").forEach((chip) => {
    chip.onclick = () => {
      view.querySelectorAll(".chip.sentiment").forEach((c) => c.classList.remove("on"));
      chip.classList.add("on");
      loadSuggestions(view, chip.dataset.sentiment);
    };
  });
  view.querySelector("#add-stock").onclick = () => {
    _builder.legs.push({
      kind: "stock", strike: null, quantity: 1, price: _builder.spot,
      symbol: null, expiration: null, bid: null, ask: null,
      delta: null, gamma: null, theta: null, vega: null,
    });
    renderLegs(view);
    computePayoff(view);
  };

  renderLegs(view);
  computePayoff(view);
  loadIncomeGrid(view); // fire-and-forget; the grid arrives when greeks do
  loadSuggestions(view, "bullish"); // Bullish chip starts selected -- load its cards immediately
}

const _TEMPLATE_LABELS = {
  long_call: "Long Call",
  long_put: "Long Put",
  short_put: "Short Put",
  covered_call: "Covered Call",
  put_vertical_credit: "Put Vertical (credit)",
  put_vertical_debit: "Put Vertical (debit)",
  call_vertical_credit: "Call Vertical (credit)",
  call_vertical_debit: "Call Vertical (debit)",
  short_straddle: "Short Straddle",
  short_strangle: "Short Strangle",
  iron_condor: "Iron Condor",
};

function _miniPayoffSvg(curve, spot) {
  if (!curve || !curve.length) return "";
  const W = 150, H = 60, PAD = 6;
  const spots = curve.map((p) => p.spot).concat([spot]);
  const pnls = curve.map((p) => p.pnl).concat([0]);
  const xMin = Math.min(...spots), xMax = Math.max(...spots);
  const yAbsMax = Math.max(1, ...pnls.map(Math.abs));
  const x = (v) => PAD + ((v - xMin) / (xMax - xMin || 1)) * (W - 2 * PAD);
  const y = (v) => H / 2 - (v / yAbsMax) * (H / 2 - PAD);
  const points = curve.map((p) => `${x(p.spot)},${y(p.pnl)}`).join(" ");
  return `<svg viewBox="0 0 ${W} ${H}" class="mini-payoff">
    <line x1="0" y1="${y(0)}" x2="${W}" y2="${y(0)}" stroke="#3a4652" stroke-width="1"/>
    <polyline points="${points}" fill="none" stroke="#7fd1a8" stroke-width="1.5"/>
  </svg>`;
}

async function loadSuggestions(view, sentiment) {
  const el = view.querySelector("#suggestion-cards");
  if (!el) return;
  el.innerHTML = '<p class="loading">Building suggestions…</p>';
  // No expiration pinned: the server defaults to the next monthly cycle at least 30 days out.
  const params = new URLSearchParams({ spot: String(_builder.spot), sentiment });
  if (_builder.iv) params.set("iv", String(_builder.iv));
  let res;
  try {
    res = await fetch(`/api/symbol/${_builder.symbol}/suggestions?${params.toString()}`).then((r) =>
      r.json()
    );
  } catch {
    el.innerHTML = "";
    return;
  }
  const fmt = (v) => (typeof v === "number" ? v.toFixed(0) : "--");
  const pct = (v) => (typeof v === "number" ? `${(v * 100).toFixed(0)}%` : "--");
  const header = res.expiration
    ? `<p class="note">Suggestions target the ${res.expiration} monthly cycle.</p>`
    : "";
  el.innerHTML =
    header +
    (res.cards || [])
      .map(
        (card, i) => `<button type="button" class="suggestion-card" data-i="${i}">
        <b>${_TEMPLATE_LABELS[card.name] || card.name}</b>
        ${_miniPayoffSvg(card.curve, _builder.spot)}
        <span class="note">${card.cost < 0 ? "Credit" : "Cost"} $${fmt(Math.abs(card.cost))} ·
          Risk ${card.max_risk.unbounded ? "∞" : "$" + fmt(Math.abs(card.max_risk.value))} ·
          POP ${pct(card.pop)}</span>
      </button>`
      )
      .join("");
  el.querySelectorAll(".suggestion-card").forEach((btn) => {
    btn.onclick = async () => {
      const card = res.cards[parseInt(btn.dataset.i, 10)];
      view.querySelector("#template-select").value = "";
      if (res.expiration && res.expiration !== _builder.expiration) {
        _builder.expiration = res.expiration;
        await loadChain(view); // keep the chain table and leg dropdowns on the card's expiration
      }
      _setLegs(view, card.legs);
    };
  });
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
          bid: cell.bid ?? null,
          ask: cell.ask ?? null,
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
  _builder.chainQuotes = {};
  _builder.chainByKey = {};
  for (const opt of data.options || []) {
    byStrike[opt.strike] ??= {};
    byStrike[opt.strike][opt.option_type] = opt;
    if (opt.greeks) _builder.chainGreeks[opt.symbol] = opt.greeks;
    if (opt.quote) _builder.chainQuotes[opt.symbol] = opt.quote;
    _builder.chainByKey[_legKey(opt.option_type === "C" ? "call" : "put", opt.strike)] = opt;
  }
  _builder.strikes = Object.keys(byStrike).map(Number).sort((a, b) => a - b);
  const strikes = Object.keys(byStrike)
    .map(Number)
    .sort((a, b) => a - b);

  const atmStrike = strikes.reduce(
    (nearest, s) => (Math.abs(s - _builder.spot) < Math.abs(nearest - _builder.spot) ? s : nearest),
    strikes[0]
  );

  const rows = strikes.map((strike) => {
    const call = byStrike[strike].C;
    const put = byStrike[strike].P;
    const mid = (opt) => (opt && opt.quote && opt.quote.mid != null ? opt.quote.mid.toFixed(2) : "--");
    return `<tr${strike === atmStrike ? ' class="builder-atm-row"' : ""}>
      <td>${call ? mid(call) : "--"}</td>
      <td>${call ? `<button class="btn-sell" data-symbol="${call.symbol}" data-strike="${strike}" data-kind="call" data-price="${call.quote?.mid ?? 0}" data-dir="-1">Sell</button>
                    <button class="btn-buy" data-symbol="${call.symbol}" data-strike="${strike}" data-kind="call" data-price="${call.quote?.mid ?? 0}" data-dir="1">Buy</button>` : ""}</td>
      <td class="builder-strike">${strike}</td>
      <td>${put ? `<button class="btn-sell" data-symbol="${put.symbol}" data-strike="${strike}" data-kind="put" data-price="${put.quote?.mid ?? 0}" data-dir="-1">Sell</button>
                   <button class="btn-buy" data-symbol="${put.symbol}" data-strike="${strike}" data-kind="put" data-price="${put.quote?.mid ?? 0}" data-dir="1">Buy</button>` : ""}</td>
      <td>${put ? mid(put) : "--"}</td>
    </tr>`;
  });
  chainEl.innerHTML = `<table><thead><tr><th>Call mid</th><th></th><th>Strike</th><th></th><th>Put mid</th></tr></thead>
    <tbody>${rows.join("")}</tbody></table>`;
  chainEl.querySelectorAll("button[data-symbol]").forEach((btn) => {
    btn.onclick = () => addLeg(view, btn.dataset);
  });
  // Chain is its own scrollable pane (see .builder-chain) -- land on the ATM row rather than
  // making the user hunt for spot among a hundred-plus strikes.
  chainEl.querySelector("tr.builder-atm-row")?.scrollIntoView({ block: "center" });
}

function addLeg(view, data) {
  const dir = parseInt(data.dir, 10);
  // Same strike/type already in the basket (matched by the exact option contract, not just
  // strike+kind, since that's what a Sell/Buy click on the chain actually refers to) -- net the
  // click into that leg's quantity instead of adding a duplicate row. A Buy against an existing
  // short (or vice versa) nets down; if that nets exactly to zero, the position is flat and the
  // leg is removed rather than left sitting at quantity 0.
  const existing = _builder.legs.find((lg) => lg.symbol === data.symbol);
  if (existing) {
    existing.quantity += dir;
    if (existing.quantity === 0) {
      _builder.legs = _builder.legs.filter((lg) => lg !== existing);
    } else {
      _reprice(existing);
    }
    renderLegs(view);
    computePayoff(view);
    return;
  }

  const greeks = (_builder.chainGreeks || {})[data.symbol] || {};
  const quote = (_builder.chainQuotes || {})[data.symbol] || {};
  _builder.legs.push({
    kind: data.kind,
    strike: parseFloat(data.strike),
    quantity: dir,
    price: parseFloat(data.price) || 0,
    symbol: data.symbol,
    expiration: _builder.expiration,
    bid: quote.bid ?? null,
    ask: quote.ask ?? null,
    delta: greeks.delta ?? null,
    gamma: greeks.gamma ?? null,
    theta: greeks.theta ?? null,
    vega: greeks.vega ?? null,
  });
  renderLegs(view);
  computePayoff(view);
}

function _applyChainOption(leg, kind, strike) {
  // Point a leg at a different listed option: refresh symbol/quote/greeks and (unless the user
  // typed a manual premium) its fill price.
  const opt = _builder.chainByKey[_legKey(kind, strike)];
  leg.kind = kind;
  leg.strike = strike;
  leg.symbol = opt ? opt.symbol : null;
  leg.expiration = _builder.expiration;
  const quote = opt && opt.quote ? opt.quote : {};
  const greeks = opt && opt.greeks ? opt.greeks : {};
  leg.bid = quote.bid ?? null;
  leg.ask = quote.ask ?? null;
  leg.delta = greeks.delta ?? null;
  leg.gamma = greeks.gamma ?? null;
  leg.theta = greeks.theta ?? null;
  leg.vega = greeks.vega ?? null;
  leg.manualPrice = false;
  _reprice(leg);
}

function renderLegs(view) {
  const tbody = view.querySelector("#leg-table tbody");
  if (!tbody) return;
  const strikeOptions = (selected) =>
    _builder.strikes
      .map((s) => `<option value="${s}" ${s === selected ? "selected" : ""}>${s}</option>`)
      .join("");
  tbody.innerHTML = _builder.legs
    .map((leg, i) => {
      if (leg.kind === "stock") {
        return `<tr data-i="${i}">
          <td><select data-f="action"><option value="1" ${leg.quantity > 0 ? "selected" : ""}>Buy</option>
            <option value="-1" ${leg.quantity < 0 ? "selected" : ""}>Sell</option></select></td>
          <td><input data-f="qty" type="number" min="1" value="${Math.abs(leg.quantity)}"></td>
          <td class="note">--</td><td class="note">--</td><td class="note">Stock</td>
          <td><input data-f="price" type="number" step="0.01" value="${leg.price.toFixed(2)}"></td>
          <td><button data-f="del" title="remove">&times;</button></td>
        </tr>`;
      }
      return `<tr data-i="${i}">
        <td><select data-f="action"><option value="1" ${leg.quantity > 0 ? "selected" : ""}>Buy</option>
          <option value="-1" ${leg.quantity < 0 ? "selected" : ""}>Sell</option></select></td>
        <td><input data-f="qty" type="number" min="1" value="${Math.abs(leg.quantity)}"></td>
        <td class="note">${leg.expiration || _builder.expiration || "--"}</td>
        <td><select data-f="strike">${strikeOptions(leg.strike)}</select></td>
        <td><select data-f="kind"><option value="call" ${leg.kind === "call" ? "selected" : ""}>Call</option>
          <option value="put" ${leg.kind === "put" ? "selected" : ""}>Put</option></select></td>
        <td><input data-f="price" type="number" step="0.01" value="${leg.price.toFixed(2)}"></td>
        <td><button data-f="del" title="remove">&times;</button></td>
      </tr>`;
    })
    .join("");
  if (_builder.legs.length) {
    tbody.innerHTML =
      `<tr class="leg-head"><th>Action</th><th>Qty</th><th>Expiry</th><th>Strike</th>` +
      `<th>Type</th><th>Premium</th><th></th></tr>` + tbody.innerHTML;
  }

  tbody.querySelectorAll("tr[data-i]").forEach((row) => {
    const leg = _builder.legs[parseInt(row.dataset.i, 10)];
    const refresh = () => {
      renderLegs(view);
      computePayoff(view);
    };
    row.querySelector('[data-f="action"]').onchange = (e) => {
      leg.quantity = Math.abs(leg.quantity) * parseInt(e.target.value, 10);
      leg.manualPrice = false;
      _reprice(leg);
      refresh();
    };
    row.querySelector('[data-f="qty"]').onchange = (e) => {
      const magnitude = Math.max(1, parseInt(e.target.value, 10) || 1);
      leg.quantity = magnitude * Math.sign(leg.quantity || 1);
      refresh();
    };
    const strikeSel = row.querySelector('[data-f="strike"]');
    if (strikeSel)
      strikeSel.onchange = (e) => {
        _applyChainOption(leg, leg.kind, parseFloat(e.target.value));
        refresh();
      };
    const kindSel = row.querySelector('[data-f="kind"]');
    if (kindSel)
      kindSel.onchange = (e) => {
        _applyChainOption(leg, e.target.value, leg.strike);
        refresh();
      };
    row.querySelector('[data-f="price"]').onchange = (e) => {
      leg.price = parseFloat(e.target.value) || leg.price;
      leg.manualPrice = true;
      refresh();
    };
    row.querySelector('[data-f="del"]').onclick = () => {
      _builder.legs.splice(parseInt(row.dataset.i, 10), 1);
      refresh();
    };
  });
}

async function _templateCall(view, params) {
  const el = view.querySelector("#builder-validation");
  const query = new URLSearchParams({
    expiration: _builder.expiration || "",
    spot: String(_builder.spot),
    ...params,
  });
  try {
    const res = await fetch(
      `/api/symbol/${_builder.symbol}/template?${query.toString()}`
    ).then((r) => r.json());
    if (!res.ok) {
      el.textContent = res.reason || "template unavailable here";
      return null;
    }
    return res.legs;
  } catch {
    el.textContent = "Could not reach the scout server -- is it still running?";
    return null;
  }
}

function _setLegs(view, legs) {
  _builder.legs = legs.map((leg) => ({ ...leg, manualPrice: false }));
  _builder.legs.forEach(_reprice);
  renderLegs(view);
  computePayoff(view);
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
    ${
      result.probable_risk_2sd != null
        ? `<span>Probable risk (2 SD move) ${fmt(result.probable_risk_2sd)}</span>`
        : ""
    }
    <span>Breakevens ${result.breakevens.map(fmt).join(", ") || "--"}</span>
    <span>POP ${pct(result.pop)} · POW ${pct(result.pow)}</span>
    <span>Return ${pct(result.raw_return)} raw · ${pct(result.annualized_return)} annualized*</span>
    ${
      result.projected_yield_12m != null
        ? `<span>12M projected yield ${pct(result.projected_yield_12m)} (option + ${pct(result.dividend_yield)} div)*</span>`
        : ""
    }
    ${
      result.score != null
        ? `<span>Score ${result.score.toFixed(0)}${result.score_is_estimated ? " (est.)" : ""}</span>`
        : ""
    }
    <span>${greeksTag} Δ ${fmt(greeks.delta)} · Θ ${fmt(greeks.theta)} · Vega ${fmt(greeks.vega)}</span>
  `;
  const cardEl = document.getElementById("builder-strategy-card");
  if (cardEl) {
    const parts = [];
    if (result.explanation) parts.push(`<p><b>Strategy:</b> ${result.explanation}</p>`);
    if (result.suggestion) parts.push(`<p>${result.suggestion}</p>`);
    if (result.greeks_text) parts.push(`<p class="note">${result.greeks_text}</p>`);
    if (result.checklist) parts.push(renderChecklist(result.checklist));
    if (result.annualized_return != null) {
      parts.push(
        '<p class="note">* Annualized assumes the same return could be repeated back-to-back all year -- a comparison metric, not a forecast.</p>'
      );
    }
    if (result.score_is_estimated) {
      parts.push(
        '<p class="note">(est.) Score uses scout\'s own risk estimate for unlimited-risk positions, not a reference-platform value.</p>'
      );
    }
    cardEl.innerHTML = parts.join("");
  }
}

const _CHECK_ICONS = { pass: "✓", warn: "!", fail: "✕" };

function renderChecklist(checklist) {
  const dots = checklist.items
    .map((i) => `<span class="check-dot ${i.status}"></span>`)
    .join("");
  const rows = checklist.items
    .map(
      (i) =>
        `<li><span class="check-icon ${i.status}">${_CHECK_ICONS[i.status]}</span> ${i.name}</li>`
    )
    .join("");
  return `<div class="checklist">
    <p><b>Strategy checklist</b> <span class="note">(${checklist.kind})</span> ${dots}</p>
    <ul>${rows}</ul>
  </div>`;
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
