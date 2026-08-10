/**
 * Scout's builder strategy surfaces, ported over the console's EOD chain
 * snapshot: sentiment suggestion cards (bullish / bearish / high IV — the
 * reference platform's three-card sets) and the short-put/covered-call
 * income grid (risk tier × DTE bucket, delta-targeted). Zero broker calls —
 * everything reads the latest chain_eod rows, so it works all evening from
 * the day's close. Strike selection prefers the stored live delta and falls
 * back to a moneyness proxy, exactly like scout's analytics/templates.py.
 */

import type { ConsoleConfig } from "../config.js";
import {
  type ChainEodOptionRow,
  chainEodMeta,
  chainEodStatus,
  readChainEod,
} from "../store/consoleDb.js";
import { breakevens, maxLoss, maxProfit, type Leg, type Extremum } from "../analytics/payoff.js";
import { pop as popOf, probBelow } from "../analytics/pop.js";

export interface TemplateLeg {
  kind: "call" | "put" | "stock";
  strike: number | null;
  quantity: number;
  price: number;
  delta: number | null;
  expiration: string | null;
  occSymbol: string | null;
}

export interface SuggestionCard {
  name: string;
  label: string;
  legs: TemplateLeg[];
  credit: number; // net option cash flow per contract (+ = credit)
  maxProfit: Extremum;
  maxRisk: Extremum;
  breakevens: number[];
  pop: number | null;
}

export interface SuggestionsPayload {
  symbol: string;
  sentiment: string;
  tradeDate: string;
  expiration: string;
  dte: number;
  spot: number;
  cards: SuggestionCard[];
}

export interface IncomeCell {
  strike: number;
  delta: number;
  mid: number | null;
  expiration: string;
  dte: number;
  pow: number | null; // probability of expiring OTM
  annualizedReturn: number | null;
  occSymbol: string;
}

export interface IncomeGridPayload {
  symbol: string;
  kind: "put" | "call";
  tradeDate: string;
  spot: number;
  buckets: Array<{ name: string; expiration: string; dte: number; tiers: Record<string, IncomeCell> }>;
}

export const SENTIMENT_TEMPLATES: Record<string, readonly string[]> = {
  // The reference platform's three-card sets (scout's api/symbol.py).
  bullish: ["long_call", "call_vertical_debit", "put_vertical_credit"],
  bearish: ["long_put", "put_vertical_debit", "call_vertical_credit"],
  high_iv: ["put_vertical_credit", "short_strangle", "call_vertical_credit"],
};

export const TEMPLATE_LABELS: Record<string, string> = {
  long_call: "Long Call",
  long_put: "Long Put",
  short_put: "Short Put",
  covered_call: "Covered Call",
  put_vertical_credit: "Put Credit Spread",
  put_vertical_debit: "Put Debit Spread",
  call_vertical_credit: "Call Credit Spread",
  call_vertical_debit: "Call Debit Spread",
  short_straddle: "Short Straddle",
  short_strangle: "Short Strangle",
  iron_condor: "Iron Condor",
};

// Income-grid tiers/buckets, reverse-engineered in scout's chain_service.
export const INCOME_TIERS: Record<string, number> = { conservative: 0.15, optimal: 0.25, aggressive: 0.35 };
export const INCOME_BUCKETS: ReadonlyArray<readonly [string, number, number]> = [
  ["short", 20, 39],
  ["medium", 40, 70],
  ["long", 71, 180],
];

const RISK_FREE = 0.04;

/** Standard OCC option symbol: padded root + YYMMDD + C/P + strike*1000. */
export function occSymbol(root: string, expiration: string, otype: "C" | "P", strike: number): string {
  const [y, m, d] = expiration.split("-");
  const k = Math.round(strike * 1000)
    .toString()
    .padStart(8, "0");
  return `${root.padEnd(6, " ")}${y!.slice(2)}${m}${d}${otype}${k}`;
}

type Opt = ChainEodOptionRow & { occ: string };

function typed(options: Opt[], otype: "C" | "P"): Opt[] {
  return options.filter((o) => o.otype === otype && o.mid !== null).sort((a, b) => a.strike - b.strike);
}

// Rough delta → OTM-fraction fallbacks (scout's _DELTA_TO_OTM).
const DELTA_TO_OTM: Record<string, number> = { "0.5": 0, "0.35": 0.03, "0.25": 0.05, "0.16": 0.08, "0.18": 0.04 };

function pick(options: Opt[], spot: number, deltaTarget: number, side: "call" | "put"): Opt | null {
  const withDelta = options.filter((o) => o.delta !== null);
  if (withDelta.length > 0) {
    return withDelta.reduce((best, o) =>
      Math.abs(Math.abs(o.delta!) - deltaTarget) < Math.abs(Math.abs(best.delta!) - deltaTarget) ? o : best,
    );
  }
  if (options.length === 0) return null;
  const otm = DELTA_TO_OTM[String(deltaTarget)] ?? 0.05;
  const target = side === "call" ? spot * (1 + otm) : spot * (1 - otm);
  return options.reduce((best, o) => (Math.abs(o.strike - target) < Math.abs(best.strike - target) ? o : best));
}

function nearestItm(options: Opt[], spot: number, side: "call" | "put"): Opt | null {
  if (options.length === 0) return null;
  if (side === "call") {
    const itm = options.filter((o) => o.strike <= spot);
    return itm.length > 0 ? itm[itm.length - 1]! : options[0]!;
  }
  const itm = options.filter((o) => o.strike >= spot);
  return itm.length > 0 ? itm[0]! : options[options.length - 1]!;
}

function distinct(...opts: Array<Opt | null>): boolean {
  const present = opts.filter((o): o is Opt => o !== null);
  if (present.length !== opts.length) return false;
  return new Set(present.map((o) => `${o.strike}|${o.otype}`)).size === present.length;
}

function leg(o: Opt, quantity: number, expiration: string): TemplateLeg {
  return {
    kind: o.otype === "C" ? "call" : "put",
    strike: o.strike,
    quantity,
    price: o.mid ?? 0,
    delta: o.delta,
    expiration,
    occSymbol: o.occ,
  };
}

/** Scout's analytics/templates.build, over one expiration's snapshot rows. */
export function buildTemplate(
  name: string,
  options: Opt[],
  spot: number,
  expiration: string,
  iv: number | null,
  dte: number,
): TemplateLeg[] | null {
  const calls = typed(options, "C");
  const puts = typed(options, "P");
  const em = iv !== null && iv > 0 && dte > 0 ? spot * iv * Math.sqrt(dte / 365) : null;

  switch (name) {
    case "long_call": {
      const o = nearestItm(calls, spot, "call");
      return o !== null ? [leg(o, 1, expiration)] : null;
    }
    case "long_put": {
      const o = nearestItm(puts, spot, "put");
      return o !== null ? [leg(o, 1, expiration)] : null;
    }
    case "short_put": {
      const o = pick(puts, spot, 0.25, "put");
      return o !== null ? [leg(o, -1, expiration)] : null;
    }
    case "put_vertical_credit": {
      const near = pick(puts, spot, 0.5, "put");
      const far = pick(puts, spot, 0.25, "put");
      if (!distinct(near, far)) return null;
      return [leg(near!, -1, expiration), leg(far!, 1, expiration)];
    }
    case "call_vertical_credit": {
      const near = pick(calls, spot, 0.5, "call");
      const far = pick(calls, spot, 0.25, "call");
      if (!distinct(near, far)) return null;
      return [leg(near!, -1, expiration), leg(far!, 1, expiration)];
    }
    case "put_vertical_debit": {
      const buy = nearestItm(puts, spot, "put");
      let sell: Opt | null = null;
      if (em !== null && buy !== null) {
        const below = puts.filter((o) => o.strike < buy.strike);
        sell =
          below.length > 0
            ? below.reduce((b, o) => (Math.abs(o.strike - (spot - em)) < Math.abs(b.strike - (spot - em)) ? o : b))
            : null;
      } else {
        sell = pick(puts, spot, 0.25, "put");
      }
      if (!distinct(buy, sell)) return null;
      return [leg(buy!, 1, expiration), leg(sell!, -1, expiration)];
    }
    case "call_vertical_debit": {
      const buy = nearestItm(calls, spot, "call");
      let sell: Opt | null = null;
      if (em !== null && buy !== null) {
        const above = calls.filter((o) => o.strike > buy.strike);
        sell =
          above.length > 0
            ? above.reduce((b, o) => (Math.abs(o.strike - (spot + em)) < Math.abs(b.strike - (spot + em)) ? o : b))
            : null;
      } else {
        sell = pick(calls, spot, 0.25, "call");
      }
      if (!distinct(buy, sell)) return null;
      return [leg(buy!, 1, expiration), leg(sell!, -1, expiration)];
    }
    case "short_strangle": {
      const call = pick(calls, spot, 0.16, "call");
      const put = pick(puts, spot, 0.16, "put");
      if (call === null || put === null || call.strike <= put.strike) return null;
      return [leg(call, -1, expiration), leg(put, -1, expiration)];
    }
    default:
      return null;
  }
}

function isMonthly(expIso: string): boolean {
  const d = new Date(expIso + "T00:00:00Z");
  return d.getUTCDay() === 5 && d.getUTCDate() >= 15 && d.getUTCDate() <= 21;
}

interface EodChain {
  tradeDate: string;
  spot: number;
  byExpiration: Map<string, Opt[]>;
}

function loadEodChain(config: ConsoleConfig, symbol: string): EodChain | null {
  const status = chainEodStatus(config);
  if (status === null) return null;
  const meta = chainEodMeta(config, status.tradeDate, symbol);
  if (meta === null) return null;
  const rows = readChainEod(config, status.tradeDate, symbol);
  const byExpiration = new Map<string, Opt[]>();
  for (const r of rows) {
    const list = byExpiration.get(r.expiration) ?? [];
    list.push({ ...r, occ: occSymbol(symbol, r.expiration, r.otype, r.strike) });
    byExpiration.set(r.expiration, list);
  }
  return { tradeDate: status.tradeDate, spot: meta.spot, byExpiration };
}

function dteOf(expiration: string): number {
  return Math.max(0, Math.round((Date.parse(expiration) - Date.now()) / 86_400_000));
}

/** ATM IV from the stored greeks: the option nearest spot that carries one. */
function atmIv(options: Opt[], spot: number): number | null {
  const withIv = options.filter((o) => o.iv !== null && o.iv > 0);
  if (withIv.length === 0) return null;
  return withIv.reduce((b, o) => (Math.abs(o.strike - spot) < Math.abs(b.strike - spot) ? o : b)).iv;
}

/** Next monthly ≥30 DTE among stored expirations, else nearest ≥30, else farthest. */
function pickSuggestionExpiration(byExpiration: Map<string, Opt[]>): string | null {
  const dated = [...byExpiration.keys()].sort();
  if (dated.length === 0) return null;
  const monthlies = dated.filter((iso) => dteOf(iso) >= 30 && isMonthly(iso));
  if (monthlies.length > 0) return monthlies[0]!;
  const farEnough = dated.filter((iso) => dteOf(iso) >= 30);
  return farEnough[0] ?? dated[dated.length - 1]!;
}

export function suggestions(
  config: ConsoleConfig,
  symbol: string,
  sentiment: string,
): SuggestionsPayload | { error: string } {
  const templates = SENTIMENT_TEMPLATES[sentiment];
  if (templates === undefined) return { error: `unknown sentiment ${sentiment}` };
  const chain = loadEodChain(config, symbol);
  if (chain === null) return { error: "no EOD chain snapshot for this symbol — run one from the screener page" };
  const expiration = pickSuggestionExpiration(chain.byExpiration);
  if (expiration === null) return { error: "snapshot holds no expirations for this symbol" };
  const options = chain.byExpiration.get(expiration)!;
  const dte = dteOf(expiration);
  const iv = atmIv(options, chain.spot);
  const t = dte / 365;

  const cards: SuggestionCard[] = [];
  for (const name of templates) {
    const legs = buildTemplate(name, options, chain.spot, expiration, iv, dte);
    if (legs === null) continue;
    const payoffLegs: Leg[] = legs.map((l) => ({
      kind: l.kind,
      quantity: l.quantity,
      price: l.price,
      strike: l.strike,
    }));
    const credit = -legs.reduce((s, l) => (l.kind === "stock" ? s : s + l.quantity * l.price), 0) * 100;
    cards.push({
      name,
      label: TEMPLATE_LABELS[name] ?? name,
      legs,
      credit,
      maxProfit: maxProfit(payoffLegs),
      maxRisk: maxLoss(payoffLegs),
      breakevens: breakevens(payoffLegs),
      pop: iv !== null ? popOf(payoffLegs, chain.spot, iv, t, RISK_FREE) : null,
    });
  }
  return { symbol, sentiment, tradeDate: chain.tradeDate, expiration, dte, spot: chain.spot, cards };
}

export function incomeGrid(
  config: ConsoleConfig,
  symbol: string,
  kind: "put" | "call",
): IncomeGridPayload | { error: string } {
  const chain = loadEodChain(config, symbol);
  if (chain === null) return { error: "no EOD chain snapshot for this symbol — run one from the screener page" };
  const otype = kind === "put" ? "P" : "C";

  const buckets: IncomeGridPayload["buckets"] = [];
  for (const [name, lo, hi] of INCOME_BUCKETS) {
    const inWindow = [...chain.byExpiration.keys()]
      .map((iso) => ({ iso, dte: dteOf(iso) }))
      .filter((e) => e.dte >= lo && e.dte <= hi)
      .sort((a, b) => a.dte - b.dte);
    if (inWindow.length === 0) continue;
    const { iso, dte } = inWindow[0]!;
    const options = typed(chain.byExpiration.get(iso)!, otype).filter((o) => o.delta !== null);
    // OTM-side band the ≤0.35-delta tiers can land in (scout's pre-narrowing).
    const banded = options.filter((o) =>
      otype === "P" ? o.strike >= 0.5 * chain.spot && o.strike <= 1.02 * chain.spot : o.strike >= 0.98 * chain.spot,
    );
    const tiers: Record<string, IncomeCell> = {};
    for (const [tier, target] of Object.entries(INCOME_TIERS)) {
      if (banded.length === 0) continue;
      const o = banded.reduce((b, c) =>
        Math.abs(Math.abs(c.delta!) - target) < Math.abs(Math.abs(b.delta!) - target) ? c : b,
      );
      const t = dte / 365;
      let pow: number | null = null;
      if (o.iv !== null && o.iv > 0) {
        const below = probBelow(chain.spot, o.strike, o.iv, t, RISK_FREE);
        pow = otype === "P" ? 1 - below : below;
      }
      let annualized: number | null = null;
      if (o.mid !== null && kind === "put" && o.strike > o.mid && dte > 0) {
        annualized = (o.mid / (o.strike - o.mid)) * (365 / dte);
      }
      tiers[tier] = {
        strike: o.strike,
        delta: Math.round(Math.abs(o.delta!) * 1000) / 1000,
        mid: o.mid,
        expiration: iso,
        dte,
        pow,
        annualizedReturn: annualized,
        occSymbol: o.occ,
      };
    }
    if (Object.keys(tiers).length > 0) buckets.push({ name, expiration: iso, dte, tiers });
  }
  return { symbol, kind, tradeDate: chain.tradeDate, spot: chain.spot, buckets };
}
