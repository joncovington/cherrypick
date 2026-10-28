/**
 * The fair-value file for the Screeners page's fair-value tab (2026-10-27): `packages/technicals`
 * `valuation` writes one file a session (`technicals/valuation/<session>.json`), one DCF estimate a
 * liquid optionable name from `cherrypick.core.valuation`. This renames its rows, badges pmcc's
 * configured symbols (pmcc's own config, never technicals'), and decides nothing. A model estimate
 * of value with no measured edge; the rows are by symbol.
 */
import fs from "node:fs";
import path from "node:path";
import type { ValuationPayload, ValuationRow, ValuationState } from "@console/shared";
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
    consensusUsed: r["consensus_used"] === true,
    flags: strings(r["flags"]),
    pmcc: pmcc.has(symbol),
  };
}

export function readValuation(config: ConsoleConfig, dayParam: string | null): ValuationPayload {
  const dir = path.join(config.paths.technicalsDir, "valuation");
  const all = days(dir);
  const day = dayParam !== null && DAY.test(dayParam) && all.includes(dayParam) ? dayParam : (all[0] ?? null);
  const empty: ValuationPayload = {
    day: null,
    days: all,
    builtAt: null,
    universe: null,
    names: 0,
    states: {},
    rule: { forecastYears: null, terminalGrowth: null, riskFree: null, equityPremium: null, beta: null, edge: null },
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
      equityPremium: num(rule["equity_premium"]),
      beta: str(rule["beta"]),
      edge: str(rule["edge"]),
    },
    rows,
  };
}
