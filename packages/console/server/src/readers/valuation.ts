/**
 * The fair-value file for the Screeners page's fair-value tab: packages/fundamentals `valuation`
 * (2026-10-29, moved from packages/technicals) writes one file a session
 * (`fundamentals/valuation/<session>.json`), one DCF estimate an options-tradable name from
 * `cherrypick.core.valuation`. This renames its rows, badges pmcc's configured symbols (pmcc's own
 * config), and decides nothing. A model estimate of value, tested 2018-26 with no edge.
 */
import fs from "node:fs";
import path from "node:path";
import type {
  ValuationBridge,
  ValuationForecastYear,
  ValuationModel,
  ValuationPayload,
  ValuationRow,
  ValuationState,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { num, str } from "./db.js";
import { pmccSymbols } from "./pmcc.js";

const DAY = /^\d{4}-\d{2}-\d{2}$/;
const STATES: readonly ValuationState[] = ["valued", "not-applicable", "no-statements", "refused"];

function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function readJson(file: string): Record<string, unknown> | null {
  try {
    return rec(JSON.parse(fs.readFileSync(file, "utf-8")));
  } catch {
    return null;
  }
}

function days(dir: string): string[] {
  try {
    return fs
      .readdirSync(dir)
      .filter((n) => /^\d{4}-\d{2}-\d{2}\.json$/.test(n))
      .map((n) => n.slice(0, 10))
      .sort()
      .reverse();
  } catch {
    return [];
  }
}

function strings(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((f): f is string => typeof f === "string") : [];
}

/** packages/fundamentals' data directory: the config's, else beside technicals' (a test config). */
function fundamentalsDir(config: ConsoleConfig): string {
  return config.paths.fundamentalsDir ?? path.join(path.dirname(config.paths.technicalsDir), "fundamentals");
}

const GROWTH_SOURCES = ["consensus", "history", "fade"] as const;

/** Version 2's working; null on an older file (no `capital`) or a non-valued row. */
function modelOf(r: Record<string, unknown>): ValuationModel | null {
  const c = rec(r["capital"]);
  if (Object.keys(c).length === 0) return null;
  const d = rec(r["drivers"]);
  const t = rec(r["terminal"]);
  const cons = rec(r["consensus"]);
  const data = rec(r["data"]);
  const grid: [number, number, number | null][] = [];
  for (const cell of Array.isArray(r["grid"]) ? r["grid"] : []) {
    if (Array.isArray(cell) && cell.length === 3 && num(cell[0]) !== null && num(cell[1]) !== null) {
      grid.push([num(cell[0])!, num(cell[1])!, num(cell[2])]);
    }
  }
  const sources: Record<string, string> = {};
  for (const [k, v] of Object.entries(rec(r["sources"]))) if (typeof v === "string") sources[k] = v;
  const disagree: Record<string, [number, number]> = {};
  for (const [k, v] of Object.entries(rec(r["disagree"]))) {
    if (Array.isArray(v) && num(v[0]) !== null && num(v[1]) !== null) disagree[k] = [num(v[0])!, num(v[1])!];
  }
  return {
    capital: {
      rf: num(c["rf"]), rfSource: str(c["rf_source"]), erp: num(c["erp"]), erpMonth: str(c["erp_month"]),
      betaRaw: num(c["beta_raw"]), beta: num(c["beta"]), ke: num(c["ke"]), coverage: num(c["coverage"]),
      rating: str(c["rating"]), spread: num(c["spread"]), ratingsAsOf: str(c["ratings_as_of"]),
      kdPreTax: num(c["kd_pre_tax"]), kdAfterTax: num(c["kd_after_tax"]), marginalTax: num(c["marginal_tax"]),
      equityWeight: num(c["equity_weight"]), debtWeight: num(c["debt_weight"]), wacc: num(c["wacc"]),
    },
    drivers: {
      ebitMargin: num(d["ebit_margin"]), daPct: num(d["da_pct"]), capexPct: num(d["capex_pct"]),
      wcPct: num(d["wc_pct"]), wcIsLevel: d["wc_is_level"] === true, taxRate: num(d["tax_rate"]),
      historyYears: num(d["history_years"]),
    },
    terminal: { fcf: num(t["fcf"]), value: num(t["value"]), reinvestmentRate: num(t["reinvestment_rate"]), roic: num(t["roic"]) },
    consensus: {
      snapshot: str(cons["snapshot"]),
      years: (Array.isArray(cons["years"]) ? cons["years"] : [])
        .map((y) => rec(y))
        .filter((y) => str(y["period_end"]) !== null && num(y["revenue"]) !== null)
        .map((y) => ({
          periodEnd: str(y["period_end"])!, revenue: num(y["revenue"])!, analysts: num(y["analysts"]),
          high: num(y["high"]), low: num(y["low"]),
        })),
    },
    history: (Array.isArray(r["history"]) ? r["history"] : [])
      .map((y) => rec(y))
      .filter((y) => str(y["end"]) !== null)
      .map((y) => ({
        end: str(y["end"])!, revenue: num(y["revenue"]), ebitMargin: num(y["ebit_margin"]), daPct: num(y["da_pct"]),
        capexPct: num(y["capex_pct"]), wcPct: num(y["wc_pct"]), taxRate: num(y["tax_rate"]),
      })),
    grid,
    data: {
      fiscalYearEnd: str(data["fiscal_year_end"]), filed: str(data["filed"]), latestPeriod: str(data["latest_period"]),
      latestFiled: str(data["latest_filed"]), sic: str(data["sic"]), sicDescription: str(data["sic_description"]),
      source: str(data["source"]),
    },
    sources,
    disagree,
    impliedWacc: num(r["implied_wacc"]),
  };
}

/** The value when it is one of the builder's words, else null (an older file, or a word it never writes). */
function oneOf<T extends string>(v: unknown, words: readonly T[]): T | null {
  return typeof v === "string" && (words as readonly string[]).includes(v) ? (v as T) : null;
}

function toRow(v: unknown, pmcc: ReadonlySet<string>): ValuationRow | null {
  const r = rec(v);
  const symbol = str(r["symbol"]);
  const state = str(r["state"]);
  if (symbol === null || state === null || !STATES.includes(state as ValuationState)) return null;
  return {
    symbol,
    type: str(r["type"]),
    sector: str(r["sector"]),
    state: state as ValuationState,
    reason: str(r["reason"]),
    spot: num(r["spot"]),
    spotDate: str(r["spot_date"]),
    fiscalYearEnd: str(r["fiscal_year_end"]),
    fairValue: num(r["fair_value"]),
    low: num(r["low"]),
    high: num(r["high"]),
    gap: num(r["gap"]),
    wacc: num(r["wacc"]),
    beta: num(r["beta"]),
    betaRaw: num(r["beta_raw"]),
    betaBroker: num(r["beta_broker"]),
    marketCap: num(r["market_cap"]),
    marketCapBroker: num(r["market_cap_broker"]),
    growthY1: num(r["growth_y1"]),
    ebitMargin: num(r["ebit_margin"]),
    terminalShare: num(r["terminal_share"]),
    impliedEvEbitda: num(r["implied_ev_ebitda"]),
    impliedMargin: num(r["implied_margin"]),
    impliedGrowth: num(r["implied_growth"]),
    impliedGrowthEdge: oneOf(r["implied_growth_edge"], ["above", "below"] as const),
    bestMargin: num(r["best_margin"]),
    bestGrowth: num(r["best_growth"]),
    reach: oneOf(r["reach"], ["below value", "within history", "beyond history"] as const),
    consensusUsed: r["consensus_used"] === true,
    terminalGrowth: num(r["terminal_growth"]),
    forecast: forecastOf(r["forecast"]),
    bridge: bridgeOf(r["bridge"]),
    model: modelOf(r),
    flags: strings(r["flags"]),
    pmcc: pmcc.has(symbol),
  };
}

/** The forecast years; null when the file has none (an older file, a non-valued row) or any year is
 *  short a figure the chart needs. */
function forecastOf(v: unknown): ValuationForecastYear[] | null {
  if (!Array.isArray(v) || v.length === 0) return null;
  const out: ValuationForecastYear[] = [];
  for (const item of v) {
    const y = rec(item);
    const year = num(y["year"]);
    const revenue = num(y["revenue"]);
    const growth = num(y["growth"]);
    const fcf = num(y["fcf"]);
    if (year === null || revenue === null || growth === null || fcf === null) return null;
    out.push({
      year, revenue, growth, ebitMargin: num(y["ebit_margin"]), fcf,
      growthSource: oneOf(y["growth_source"], GROWTH_SOURCES), ebit: num(y["ebit"]), nopat: num(y["nopat"]),
      da: num(y["da"]), capex: num(y["capex"]), wcChange: num(y["wc_change"]), discount: num(y["discount"]),
      pv: num(y["pv"]),
    });
  }
  return out;
}

/** The bridge, whole or not at all: a part-bridge would not add up to the value. */
function bridgeOf(v: unknown): ValuationBridge | null {
  const b = rec(v);
  const keys = ["pv_forecast", "pv_terminal", "ev", "debt", "cash", "equity", "shares"] as const;
  const n = keys.map((k) => num(b[k]));
  if (n.some((x) => x === null)) return null;
  const [pvForecast, pvTerminal, ev, debt, cash, equity, shares] = n as number[];
  const opt = (k: string) => num(b[k]) ?? 0;
  return {
    pvForecast: pvForecast!, pvTerminal: pvTerminal!, ev: ev!, debt: debt!, cash: cash!, equity: equity!, shares: shares!,
    financeLease: opt("finance_lease"), preferred: opt("preferred"), nci: opt("nci"),
    basicShares: num(b["basic_shares"]) ?? shares!, rsu: opt("rsu"), optionShares: opt("option_shares"),
  };
}

export function readValuation(config: ConsoleConfig, dayParam: string | null): ValuationPayload {
  const dir = path.join(fundamentalsDir(config), "valuation");
  const all = days(dir);
  const day = dayParam !== null && DAY.test(dayParam) && all.includes(dayParam) ? dayParam : (all[0] ?? null);
  const empty: ValuationPayload = {
    day: null,
    days: all,
    builtAt: null,
    universe: null,
    names: 0,
    states: {},
    rule: {
      forecastYears: null,
      terminalGrowth: null,
      riskFree: null,
      riskFreeSource: null,
      equityPremium: null,
      beta: null,
      edge: null,
    },
    rows: [],
  };
  if (day === null) return empty;
  const doc = readJson(path.join(dir, `${day}.json`));
  if (doc === null) return empty;
  const pmcc = new Set(pmccSymbols(config));
  const rule = rec(doc["rule"]);
  const states: Record<string, number> = {};
  for (const [k, v] of Object.entries(rec(doc["states"]))) {
    const n = num(v);
    if (n !== null) states[k] = n;
  }
  const rows = Object.values(rec(doc["rows"]))
    .map((r) => toRow(r, pmcc))
    .filter((r): r is ValuationRow => r !== null)
    .sort((a, b) => a.symbol.localeCompare(b.symbol));
  return {
    day,
    days: all,
    builtAt: str(doc["built_at"]),
    universe: str(doc["universe"]),
    names: num(doc["names"]) ?? rows.length,
    states,
    rule: {
      forecastYears: num(rule["forecast_years"]),
      terminalGrowth: num(rule["terminal_growth"]),
      riskFree: num(rule["risk_free"]),
      riskFreeSource: str(rule["risk_free_source"]),
      equityPremium: num(rule["equity_premium"]),
      beta: str(rule["beta"]),
      edge: str(rule["edge"]),
    },
    rows,
  };
}
