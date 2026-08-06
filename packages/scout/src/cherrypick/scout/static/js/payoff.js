"use strict";

/* ---------------- the leg-basket builder: chain picker + SVG payoff diagram ----------------
 * State lives in one Alpine component (builderStore(), wired via x-data on #builder-view --
 * see templates/builder.html) instead of a module-level mutable object. The leg table is
 * declarative (x-for/x-model in the template); everything else here still renders into an HTML
 * string bound via x-html, since chain/income-grid/chart/checklist are display-only and
 * regenerated wholesale on every change anyway -- converting those to templating would be a much
 * larger, riskier diff for no real reactivity benefit. Every state-mutating method still ends
 * with an explicit computePayoff() call (Alpine's DOM bindings update the leg table and readonly
 * fields automatically; computePayoff's own fetch/render is a side effect, not something Alpine's
 * reactivity is asked to infer).
 */

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

const _TIER_LABELS = { conservative: "Conservative", optimal: "Optimal", aggressive: "Aggressive" };
const _BUCKET_LABELS = { short: "20–39d", medium: "40–70d", long: "71–180d" };
const _CHECK_ICONS = { pass: "✓", warn: "!", fail: "✕" };

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

// Round tick step (1/2/2.5/5/10 x a power of ten), same shape as flies' dashboard.py ticksFor --
// an axis of raw endpoints-only doesn't read as a scale, and matching that chart's tick
// algorithm here is exactly what "look more like the flies viz" asked for.
function _niceTicks(min, max, count) {
  const span = max - min || 1;
  const raw = span / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push({ v, step });
  return out;
}

function renderPayoffSvgString(result, spot) {
  const curve = result.curve;
  if (!curve.length) return "";
  const W = 640,
    H = 280;
  const pad = { l: 54, r: 10, t: 14, b: 22 };

  // payoff_curve only returns the exact kink points (one per strike) -- connecting just those
  // draws a naked diagonal across the whole visible window instead of the true flat-or-sloped
  // tail beyond the outermost strike. Widen the window a bit past the strikes and extrapolate
  // both ends using the API's own analytic tail slopes (flat for a defined-risk spread,
  // genuinely sloped for an uncapped naked leg) -- the same tail math breakevens() already
  // relies on internally, now drawn instead of only used to find a crossing.
  const strikeSpan = curve[curve.length - 1].spot - curve[0].spot || Math.max(1, curve[0].spot * 0.1);
  const tailPad = Math.max(strikeSpan * 0.15, 1);
  const first = curve[0],
    last = curve[curve.length - 1];
  const slopeBelow = result.slope_below ?? 0,
    slopeAbove = result.slope_above ?? 0;
  const extended = [
    { spot: first.spot - tailPad, pnl: first.pnl - slopeBelow * tailPad },
    ...curve,
    { spot: last.spot + tailPad, pnl: last.pnl + slopeAbove * tailPad },
  ];

  const spots = extended.map((p) => p.spot).concat([spot]);
  const pnls = extended.map((p) => p.pnl).concat([0]);
  const xMin = Math.min(...spots),
    xMax = Math.max(...spots);
  const yAbsMax = Math.max(1, ...pnls.map(Math.abs)) * 1.1;

  const X = (v) => pad.l + ((v - xMin) / (xMax - xMin || 1)) * (W - pad.l - pad.r);
  const Y = (v) => H - pad.b - ((v + yAbsMax) / (2 * yAbsMax || 1)) * (H - pad.t - pad.b);
  const zeroY = Y(0);

  let grid = "";
  let labels = "";
  _niceTicks(-yAbsMax, yAbsMax, 5).forEach(({ v }) => {
    const y = Y(v);
    grid += `<line x1="${pad.l}" y1="${y}" x2="${W - pad.r}" y2="${y}" stroke="${Math.abs(v) < 1e-9 ? "#3d4653" : "#1c222b"}"/>`;
    labels += `<text x="4" y="${y + 3}" fill="#8b949e" font-size="10">${v < 0 ? "-" : ""}$${Math.abs(v).toFixed(0)}</text>`;
  });
  _niceTicks(xMin, xMax, 6).forEach(({ v, step }) => {
    const x = X(v);
    grid += `<line x1="${x}" y1="${pad.t}" x2="${x}" y2="${H - pad.b}" stroke="#1c222b"/>`;
    const text = Number.isInteger(step) ? v.toFixed(0) : v.toFixed(2);
    labels += `<text x="${x - text.length * 3}" y="${H - 6}" fill="#8b949e" font-size="10">${text}</text>`;
  });

  // Green fill above zero, red below -- the (tail-extended) curve clamped to each side and
  // closed back to the zero line at both ends, same construction flies' drawPayoff uses.
  const above = extended.map((p) => `${X(p.spot)},${Y(Math.max(p.pnl, 0))}`).join(" ");
  const below = extended.map((p) => `${X(p.spot)},${Y(Math.min(p.pnl, 0))}`).join(" ");
  const firstX = X(extended[0].spot),
    lastX = X(extended[extended.length - 1].spot);

  const points = extended.map((p) => `${X(p.spot)},${Y(p.pnl)}`).join(" ");
  const spotX = X(spot);
  const spotLabel = `spot ${spot.toFixed(2)}`;
  const labelW = spotLabel.length * 5.5 + 10;
  const labelX = Math.min(Math.max(spotX - labelW / 2, pad.l), W - pad.r - labelW);

  return `
    ${grid}
    <polygon points="${firstX},${zeroY} ${above} ${lastX},${zeroY}" fill="rgba(63,185,80,.28)"/>
    <polygon points="${firstX},${zeroY} ${below} ${lastX},${zeroY}" fill="rgba(248,81,73,.24)"/>
    <line x1="${spotX}" y1="${pad.t}" x2="${spotX}" y2="${H - pad.b}" stroke="#e3b341" stroke-width="1.5" stroke-dasharray="4,3" opacity="0.85"/>
    <polyline points="${points}" fill="none" stroke="#7fd1a8" stroke-width="1.8"/>
    ${labels}
    <rect x="${labelX}" y="${pad.t}" width="${labelW}" height="15" rx="3" fill="rgba(13,17,23,.92)" stroke="#3d4653"/>
    <text x="${labelX + 5}" y="${pad.t + 11}" fill="#e3b341" font-size="10">${spotLabel}</text>
  `;
}

function renderMetricsHtml(result) {
  const fmt = (v) => (typeof v === "number" ? v.toFixed(2) : "--");
  const pct = (v) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "--");
  // Live DXLink greeks (attached per leg from the chain) beat model greeks when present.
  const live = result.net_greeks && result.net_greeks.delta != null;
  const greeks = (live ? result.net_greeks : result.model_greeks) || {};
  const greeksTag = live ? "Live" : "Model";
  return `
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
}

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

function renderStrategyCardHtml(result) {
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
  return parts.join("");
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

function builderStore() {
  return {
    symbol: null,
    dates: [],
    expiration: null,
    templateName: "",
    priceBy: "mid",
    spot: null,
    iv: null,
    ivRankLabel: "--",
    legs: [],
    strikes: [],
    chainGreeks: {},
    chainQuotes: {},
    chainByKey: {},
    lastResult: null,
    chainHtml: '<p class="loading">Loading option chain…</p>',
    chainCollapsed: true,
    sentiment: "bullish",
    suggestionCards: [],
    suggestionHeader: "",
    suggestionsLoading: false,
    _suggestionExpiration: null,
    incomeGridHtml: "",
    warningsHtml: "",
    chartSvg: "",
    metricsHtml: '<span class="note">Add a leg to see the payoff.</span>',
    strategyCardHtml: "",
    note: "",
    validationHtml: "Staging only -- no order is ever submitted.",

    async init() {
      this.symbol = this.$el.dataset.symbol;
      let quote, expirations;
      try {
        // /quote, not /stats -- the builder only needs spot + IV, and /stats is candle-history-
        // backed (week52 range, avg volume) which means a cold DXLink backfill on every symbol
        // selection.
        [quote, expirations] = await Promise.all([
          fetch(`/api/symbol/${this.symbol}/quote`).then((r) => r.json()),
          fetch(`/api/symbol/${this.symbol}/expirations`).then((r) => r.json()),
        ]);
      } catch {
        this.chainHtml = "<p>Could not reach the scout server -- is it still running?</p>";
        return;
      }
      this.spot = quote.last ?? 100;
      this.iv = quote.iv_30d ?? 0.3;
      // IVR (IV Rank) is a 0..100 percentile, shown in the "NN/100" form the reference platform's
      // own info bar uses, not the raw IV decimal (this.iv, still used internally for POP/
      // expected-move/template calcs -- a different number that just happens to share the "IV" name).
      const ivRank = quote.iv_rank != null ? Math.round(parseFloat(quote.iv_rank) * 100) : null;
      this.ivRankLabel = ivRank != null ? `${ivRank}/100` : "--";

      this.dates = Object.keys(expirations.expirations || {}).sort();
      if (this.dates.length) {
        // Same default the sentiment suggestion cards use (next standard monthly >= 30 DTE, else
        // nearest >= 30 DTE, else farthest listed) -- the API computes it once so both agree.
        this.expiration =
          expirations.default_expiration && this.dates.includes(expirations.default_expiration)
            ? expirations.default_expiration
            : this.dates[0];
        await this.loadChain();
      } else {
        this.chainHtml = "<p>No option chain available for this symbol.</p>";
      }
      this.loadIncomeGrid(); // fire-and-forget; the grid arrives when greeks do
      this.loadSuggestions("bullish"); // Bullish chip starts selected -- load its cards immediately
    },

    _legKey(kind, strike) {
      return `${kind}:${strike}`;
    },

    // Fill price under the current price-by mode: mid, or the "natural" side (sell at bid, buy at ask).
    _fillPrice(leg) {
      const quote = this.chainQuotes[leg.symbol];
      if (!quote) return leg.price;
      if (this.priceBy === "mid") return quote.mid ?? leg.price;
      return (leg.quantity < 0 ? quote.bid : quote.ask) ?? leg.price;
    },

    _reprice(leg) {
      if (!leg.manualPrice && leg.kind !== "stock") leg.price = this._fillPrice(leg) ?? leg.price;
    },

    _daysToExpiration(expirationIso) {
      const ms = new Date(expirationIso + "T00:00:00Z") - new Date();
      return Math.max(0, ms / 86400000);
    },

    // Point a leg at a different listed option: refresh symbol/quote/greeks and (unless the user
    // typed a manual premium) its fill price.
    _applyChainOption(leg, kind, strike) {
      const opt = this.chainByKey[this._legKey(kind, strike)];
      leg.kind = kind;
      leg.strike = strike;
      leg.symbol = opt ? opt.symbol : null;
      leg.expiration = this.expiration;
      const quote = opt && opt.quote ? opt.quote : {};
      const greeks = opt && opt.greeks ? opt.greeks : {};
      leg.bid = quote.bid ?? null;
      leg.ask = quote.ask ?? null;
      leg.delta = greeks.delta ?? null;
      leg.gamma = greeks.gamma ?? null;
      leg.theta = greeks.theta ?? null;
      leg.vega = greeks.vega ?? null;
      leg.manualPrice = false;
      this._reprice(leg);
    },

    _setLegs(legs) {
      this.legs = legs.map((leg) => ({ ...leg, manualPrice: false }));
      this.legs.forEach((leg) => this._reprice(leg));
      this.computePayoff();
    },

    async loadChain() {
      this.loadWarnings(); // fire-and-forget alongside the chain fetch
      this.chainHtml = '<p class="loading">Loading option chain…</p>';
      let data;
      try {
        data = await fetch(
          `/api/symbol/${this.symbol}/chain?expiration=${encodeURIComponent(this.expiration)}`
        ).then((r) => r.json());
      } catch {
        this.chainHtml = "<p>Could not reach the scout server -- is it still running?</p>";
        return;
      }
      if (!data.options || !data.options.length) {
        this.chainHtml = "<p>No strikes available for this expiration.</p>";
        return;
      }

      const byStrike = {};
      this.chainGreeks = {};
      this.chainQuotes = {};
      this.chainByKey = {};
      for (const opt of data.options || []) {
        byStrike[opt.strike] ??= {};
        byStrike[opt.strike][opt.option_type] = opt;
        if (opt.greeks) this.chainGreeks[opt.symbol] = opt.greeks;
        if (opt.quote) this.chainQuotes[opt.symbol] = opt.quote;
        this.chainByKey[this._legKey(opt.option_type === "C" ? "call" : "put", opt.strike)] = opt;
      }
      const strikes = Object.keys(byStrike).map(Number).sort((a, b) => a - b);
      this.strikes = strikes;

      const atmStrike = strikes.reduce(
        (nearest, s) => (Math.abs(s - this.spot) < Math.abs(nearest - this.spot) ? s : nearest),
        strikes[0]
      );

      const mid = (opt) => (opt && opt.quote && opt.quote.mid != null ? opt.quote.mid.toFixed(2) : "--");
      const rows = strikes.map((strike) => {
        const call = byStrike[strike].C;
        const put = byStrike[strike].P;
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
      this.chainHtml = `<table><thead><tr><th>Call mid</th><th></th><th>Strike</th><th></th><th>Put mid</th></tr></thead>
        <tbody>${rows.join("")}</tbody></table>`;

      // Chain is its own scrollable pane (see .builder-chain) -- land on the ATM row rather than
      // making the user hunt for spot among a hundred-plus strikes. Wired after Alpine flushes
      // the x-html update, since the buttons don't exist in the DOM until then.
      this.$nextTick(() => {
        const chainEl = this.$refs.chain;
        if (!chainEl) return;
        chainEl.querySelectorAll("button[data-symbol]").forEach((btn) => {
          btn.onclick = () => this.addLegFromChain(btn.dataset);
        });
        chainEl.querySelector("tr.builder-atm-row")?.scrollIntoView({ block: "center" });
      });
    },

    toggleChain() {
      this.chainCollapsed = !this.chainCollapsed;
      if (!this.chainCollapsed) {
        this.$nextTick(() =>
          this.$refs.chain?.querySelector("tr.builder-atm-row")?.scrollIntoView({ block: "center" })
        );
      }
    },

    addLegFromChain(data) {
      const dir = parseInt(data.dir, 10);
      // Same strike/type already in the basket (matched by the exact option contract, not just
      // strike+kind, since that's what a Sell/Buy click on the chain actually refers to) -- net
      // the click into that leg's quantity instead of adding a duplicate row. A Buy against an
      // existing short (or vice versa) nets down; if that nets exactly to zero, the position is
      // flat and the leg is removed rather than left sitting at quantity 0.
      const existing = this.legs.find((lg) => lg.symbol === data.symbol);
      if (existing) {
        existing.quantity += dir;
        if (existing.quantity === 0) {
          this.legs = this.legs.filter((lg) => lg !== existing);
        } else {
          this._reprice(existing);
        }
        this.computePayoff();
        return;
      }
      const greeks = (this.chainGreeks || {})[data.symbol] || {};
      const quote = (this.chainQuotes || {})[data.symbol] || {};
      this.legs.push({
        kind: data.kind,
        strike: parseFloat(data.strike),
        quantity: dir,
        price: parseFloat(data.price) || 0,
        symbol: data.symbol,
        expiration: this.expiration,
        bid: quote.bid ?? null,
        ask: quote.ask ?? null,
        delta: greeks.delta ?? null,
        gamma: greeks.gamma ?? null,
        theta: greeks.theta ?? null,
        vega: greeks.vega ?? null,
      });
      this.computePayoff();
    },

    setLegAction(leg, dir) {
      leg.quantity = Math.abs(leg.quantity) * dir;
      leg.manualPrice = false;
      this._reprice(leg);
      this.computePayoff();
    },

    setLegQty(leg, rawValue) {
      const magnitude = Math.max(1, parseInt(rawValue, 10) || 1);
      leg.quantity = magnitude * Math.sign(leg.quantity || 1);
      this.computePayoff();
    },

    setLegStrike(leg, strike) {
      this._applyChainOption(leg, leg.kind, strike);
      this.computePayoff();
    },

    setLegKind(leg, kind) {
      this._applyChainOption(leg, kind, leg.strike);
      this.computePayoff();
    },

    setLegPrice(leg, rawValue) {
      leg.price = parseFloat(rawValue) || leg.price;
      leg.manualPrice = true;
      this.computePayoff();
    },

    removeLeg(i) {
      this.legs.splice(i, 1);
      this.computePayoff();
    },

    addOption(kind) {
      if (!this.strikes.length) return;
      const atm = this.strikes.reduce((a, b) =>
        Math.abs(b - this.spot) < Math.abs(a - this.spot) ? b : a
      );
      const leg = { kind, strike: atm, quantity: 1, price: 0 };
      this._applyChainOption(leg, kind, atm);
      this.legs.push(leg);
      this.computePayoff();
    },

    addStock() {
      this.legs.push({
        kind: "stock", strike: null, quantity: 1, price: this.spot,
        symbol: null, expiration: null, bid: null, ask: null,
        delta: null, gamma: null, theta: null, vega: null,
      });
      this.computePayoff();
    },

    priceByChange() {
      this.legs.forEach((leg) => {
        leg.manualPrice = false;
        this._reprice(leg);
      });
      this.computePayoff();
    },

    async _templateCall(params) {
      const query = new URLSearchParams({
        expiration: this.expiration || "",
        spot: String(this.spot),
        ...params,
      });
      try {
        const res = await fetch(
          `/api/symbol/${this.symbol}/template?${query.toString()}`
        ).then((r) => r.json());
        if (!res.ok) {
          this.validationHtml = res.reason || "template unavailable here";
          return null;
        }
        return res.legs;
      } catch {
        this.validationHtml = "Could not reach the scout server -- is it still running?";
        return null;
      }
    },

    async templateChange() {
      if (!this.templateName) return;
      const params = { action: "build", name: this.templateName };
      if (this.iv) params.iv = String(this.iv);
      const dte = this.expiration ? this._daysToExpiration(this.expiration) : null;
      if (dte) params.template_dte = String(dte);
      const legs = await this._templateCall(params);
      if (legs) this._setLegs(legs);
    },

    async flipStrategy() {
      if (!this.legs.length) return;
      const legs = await this._templateCall({ action: "flip", legs: JSON.stringify(this.legs) });
      if (legs) {
        this.templateName = "";
        this._setLegs(legs);
      }
    },

    async widthChange(step) {
      if (!this.legs.length) return;
      const legs = await this._templateCall({
        action: "width",
        step: String(step),
        legs: JSON.stringify(this.legs),
      });
      if (legs) this._setLegs(legs);
    },

    resetLegs() {
      this.templateName = "";
      this._setLegs([]);
    },

    templateLabel(name) {
      return _TEMPLATE_LABELS[name] || name;
    },

    suggestionCardNote(card) {
      const fmt = (v) => (typeof v === "number" ? v.toFixed(0) : "--");
      const pct = (v) => (typeof v === "number" ? `${(v * 100).toFixed(0)}%` : "--");
      return (
        `${card.cost < 0 ? "Credit" : "Cost"} $${fmt(Math.abs(card.cost))} · ` +
        `Risk ${card.max_risk.unbounded ? "∞" : "$" + fmt(Math.abs(card.max_risk.value))} · POP ${pct(card.pop)}`
      );
    },

    miniPayoffSvg(curve) {
      return _miniPayoffSvg(curve, this.spot);
    },

    async loadSuggestions(sentiment) {
      this.sentiment = sentiment;
      this.suggestionsLoading = true;
      // No expiration pinned: the server defaults to the next monthly cycle at least 30 days out.
      const params = new URLSearchParams({ spot: String(this.spot), sentiment });
      if (this.iv) params.set("iv", String(this.iv));
      let res;
      try {
        res = await fetch(
          `/api/symbol/${this.symbol}/suggestions?${params.toString()}`
        ).then((r) => r.json());
      } catch {
        this.suggestionsLoading = false;
        this.suggestionCards = [];
        return;
      }
      this.suggestionsLoading = false;
      this.suggestionHeader = res.expiration
        ? `Suggestions target the ${res.expiration} monthly cycle.`
        : "";
      this._suggestionExpiration = res.expiration || null;
      this.suggestionCards = res.cards || [];
    },

    async applySuggestion(i) {
      const card = this.suggestionCards[i];
      this.templateName = "";
      if (this._suggestionExpiration && this._suggestionExpiration !== this.expiration) {
        this.expiration = this._suggestionExpiration;
        await this.loadChain(); // keep the chain table and leg dropdowns on the card's expiration
      }
      this._setLegs(card.legs);
    },

    async loadIncomeGrid() {
      if (!this.spot) return;
      this.incomeGridHtml = '<p class="loading">Loading income grid…</p>';
      let res;
      try {
        res = await fetch(
          `/api/symbol/${this.symbol}/income-grid?spot=${this.spot}&kind=put`
        ).then((r) => r.json());
      } catch {
        this.incomeGridHtml = "";
        return;
      }
      const buckets = ["short", "medium", "long"].filter((b) => res.grid && res.grid[b]);
      if (!buckets.length) {
        this.incomeGridHtml = "";
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
      this.incomeGridHtml = html;
      this.$nextTick(() => {
        this.$refs.incomeGrid?.querySelectorAll(".grid-cell").forEach((btn) => {
          btn.onclick = () => {
            const cell = JSON.parse(btn.dataset.cell);
            this.legs = [
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
            this.expiration = cell.expiration;
            this.computePayoff();
            this.loadWarnings();
          };
        });
      });
    },

    async loadWarnings() {
      if (!this.expiration) return;
      try {
        const res = await fetch(
          `/api/symbol/${this.symbol}/warnings?expiration=${encodeURIComponent(this.expiration)}`
        ).then((r) => r.json());
        this.warningsHtml = (res.warnings || []).map((w) => `<p class="notice">${w}</p>`).join("");
      } catch {
        this.warningsHtml = ""; // warnings are best-effort; their absence must not block the builder
      }
    },

    async computePayoff() {
      if (!this.legs.length) {
        this.chartSvg = "";
        this.metricsHtml = '<span class="note">Add a leg to see the payoff.</span>';
        this.strategyCardHtml = "";
        return;
      }
      const dte = this.expiration ? this._daysToExpiration(this.expiration) : null;
      const params = new URLSearchParams({
        legs: JSON.stringify(this.legs),
        spot: String(this.spot),
      });
      if (dte != null) params.set("dte", String(dte));
      if (this.iv != null) params.set("iv", String(this.iv));
      if (this.symbol) params.set("symbol", this.symbol);
      if (this.expiration) params.set("expiration", this.expiration);

      let result;
      try {
        result = await fetch(`/api/payoff?${params.toString()}`).then((r) => r.json());
      } catch {
        this.chartSvg = "";
        this.metricsHtml = '<span class="notice">Could not reach the scout server -- is it still running?</span>';
        return;
      }
      this.lastResult = result;
      this.chartSvg = renderPayoffSvgString(result, this.spot);
      this.metricsHtml = renderMetricsHtml(result);
      this.strategyCardHtml = renderStrategyCardHtml(result);
    },

    /* ---------------- order staging: validate (dry-run) and stage a leg basket ---------------- */

    _orderLegs() {
      return this.legs.map((leg) => ({ symbol: leg.symbol, quantity: leg.quantity, price: leg.price }));
    },

    _netCredit() {
      // Total dollar credit (positive) or debit (negative) across the basket at 1x -- the same
      // *100 convention the screener's `credit` column uses, not the per-share order price.
      return -this.legs.reduce((sum, leg) => sum + leg.quantity * leg.price, 0) * 100;
    },

    async validateOrder() {
      if (!this.legs.length) {
        this.validationHtml = "Add a leg first.";
        return;
      }
      this.validationHtml = "Validating…";
      try {
        const result = await postJson("/api/order/dry-run", { legs: this._orderLegs() });
        this.validationHtml = renderDryRunSummary(result);
      } catch {
        this.validationHtml = '<span class="notice">Could not reach the scout server -- is it still running?</span>';
      }
    },

    async stageTicket() {
      if (!this.legs.length) {
        this.validationHtml = "Add a leg first.";
        return;
      }
      const note = this.note.trim() || null;
      const result = this.lastResult;
      const maxRisk =
        result && result.max_loss && !result.max_loss.unbounded ? Math.abs(result.max_loss.value) : null;
      this.validationHtml = "Staging…";
      try {
        const res = await postJson("/api/staged", {
          symbol: this.symbol,
          strategy: "custom",
          legs: this._orderLegs(),
          credit: this._netCredit(),
          max_risk: maxRisk,
          note,
        });
        this.validationHtml = res.ok
          ? `Staged. ${renderDryRunSummary(res.ticket.dry_run)}`
          : `<span class="notice">${res.error || "stage failed"}</span>`;
      } catch {
        this.validationHtml = '<span class="notice">Could not reach the scout server -- is it still running?</span>';
      }
    },
  };
}
