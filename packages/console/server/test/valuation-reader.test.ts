import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { readValuation } from "../src/readers/valuation.js";

/**
 * The fair-value file: the reader renames `technicals valuation`'s file, badges pmcc's configured
 * symbols from pmcc's own config, and decides nothing.
 */

let tmp: string;
let config: ConsoleConfig;

function write(name: string, doc: unknown): void {
  const dir = path.join(tmp, "technicals", "valuation");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, name), JSON.stringify(doc));
}

const VALUED = {
  symbol: "AMZN",
  type: "Stock",
  sector: "Consumer Discretionary",
  state: "valued",
  reason: null,
  spot: 220.5,
  spot_date: "2026-10-27",
  fiscal_year_end: "2025-12-31",
  fair_value: 190.25,
  low: 150.1,
  high: 260.9,
  gap: 0.159,
  wacc: 0.0912,
  beta: 1.21,
  beta_raw: 1.21,
  beta_broker: 1.48,
  market_cap: 2.3e12,
  market_cap_broker: 2.35e12,
  growth_y1: 0.11,
  ebit_margin: 0.104,
  terminal_share: 0.71,
  implied_ev_ebitda: 18.4,
  implied_margin: 0.142,
  implied_growth: 0.2,
  implied_growth_edge: null,
  best_margin: 0.112,
  best_growth: 0.13,
  reach: "beyond history",
  consensus_used: true,
  flags: ["beta_disagrees"],
};

function doc(session: string, rows: Record<string, unknown>) {
  return {
    version: 1,
    session,
    built_at: "2026-10-27T10:28:00Z",
    universe: "public list of 2026-10-27",
    names: Object.keys(rows).length,
    states: { valued: 1, "not-applicable: fund": 1 },
    rule: { forecast_years: 5, terminal_growth: 0.02, risk_free: 0.04, equity_premium: 0.05, beta: "OLS weekly", edge: "none measured" },
    rows,
  };
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-valuation-"));
  const pmccConfig = path.join(tmp, "pmcc.json");
  fs.writeFileSync(pmccConfig, JSON.stringify({ symbols: ["XSP", "AMZN"], defaults: {} }));
  config = { paths: { technicalsDir: path.join(tmp, "technicals"), pmccConfigCandidates: [pmccConfig] } } as unknown as ConsoleConfig;
});

describe("readValuation", () => {
  it("renames the newest session's rows by symbol, badges pmcc's names and keeps every state", () => {
    write("2026-10-26.json", doc("2026-10-26", {}));
    write(
      "2026-10-27.json",
      doc("2026-10-27", {
        XSP: { symbol: "XSP", type: "Exchange Traded Fund", state: "not-applicable", reason: "fund", flags: [] },
        AMZN: VALUED,
        BNK: { symbol: "BNK", type: "Stock", sector: "Financials", state: "not-applicable", reason: "financials" },
        LOSS: { symbol: "LOSS", type: "Stock", state: "refused", reason: "no_operating_profit" },
        JUNK: { symbol: "JUNK", state: "maybe" },
      }),
    );
    const p = readValuation(config, null);
    expect(p.day).toBe("2026-10-27");
    expect(p.days).toEqual(["2026-10-27", "2026-10-26"]);
    expect(p.rule).toEqual({ forecastYears: 5, terminalGrowth: 0.02, riskFree: 0.04, riskFreeSource: null, equityPremium: 0.05, beta: "OLS weekly", edge: "none measured" });
    expect(p.states).toEqual({ valued: 1, "not-applicable: fund": 1 });
    expect(p.rows.map((r) => [r.symbol, r.state, r.reason, r.pmcc])).toEqual([
      ["AMZN", "valued", null, true],
      ["BNK", "not-applicable", "financials", false],
      ["LOSS", "refused", "no_operating_profit", false],
      ["XSP", "not-applicable", "fund", true],
    ]);
    expect(p.rows[0]).toMatchObject({
      spot: 220.5,
      fairValue: 190.25,
      low: 150.1,
      high: 260.9,
      gap: 0.159,
      wacc: 0.0912,
      beta: 1.21,
      betaBroker: 1.48,
      growthY1: 0.11,
      terminalShare: 0.71,
      impliedEvEbitda: 18.4,
      impliedMargin: 0.142,
      impliedGrowth: 0.2,
      impliedGrowthEdge: null,
      bestMargin: 0.112,
      bestGrowth: 0.13,
      reach: "beyond history",
      consensusUsed: true,
      flags: ["beta_disagrees"],
    });
  });

  it("an older day is reachable; an unknown day falls back to the newest; nothing built is empty, not an error", () => {
    expect(readValuation(config, null)).toMatchObject({ day: null, days: [], rows: [] });
    write("2026-10-26.json", doc("2026-10-26", { OLD: { ...VALUED, symbol: "OLD" } }));
    write("2026-10-27.json", doc("2026-10-27", { AMZN: VALUED }));
    expect(readValuation(config, "2026-10-26").rows[0].symbol).toBe("OLD");
    expect(readValuation(config, "2026-01-01").day).toBe("2026-10-27");
  });

  it("reads a file built before the reverse DCF, or a word it never writes, as null", () => {
    const old: Record<string, unknown> = { ...VALUED };
    for (const k of ["implied_margin", "implied_growth", "implied_growth_edge", "best_margin", "best_growth", "reach"]) delete old[k];
    write("2026-10-27.json", doc("2026-10-27", { AMZN: old, ODD: { ...VALUED, symbol: "ODD", reach: "maybe" } }));
    const rows = readValuation(config, null).rows;
    expect(rows[0]).toMatchObject({ impliedMargin: null, impliedGrowth: null, impliedGrowthEdge: null, bestMargin: null, reach: null });
    expect(rows[1].reach).toBeNull();
  });

  it("renames the forecast and the bridge, and reads a part-bridge or an older file as null", () => {
    const bridge = { pv_forecast: 4e11, pv_terminal: 9.8e11, ev: 1.38e12, debt: 1.5e11, cash: 9e10, equity: 1.32e12, shares: 1.06e10 };
    const forecast = [1, 2, 3, 4, 5].map((year) => ({ year, revenue: 7e11 + year * 4e10, growth: 0.09, ebit_margin: 0.104, fcf: 5e10 + year * 4e9 }));
    write(
      "2026-10-27.json",
      doc("2026-10-27", {
        AMZN: { ...VALUED, terminal_growth: 0.02, forecast, bridge },
        PART: { ...VALUED, symbol: "PART", bridge: { ...bridge, cash: null }, forecast: [{ year: 1, revenue: 1 }] },
        OLD: { ...VALUED, symbol: "OLD" },
      }),
    );
    const [amzn, old, part] = readValuation(config, null).rows;
    expect(amzn!.terminalGrowth).toBe(0.02);
    expect(amzn!.bridge).toEqual({ pvForecast: 4e11, pvTerminal: 9.8e11, ev: 1.38e12, debt: 1.5e11, cash: 9e10, equity: 1.32e12, shares: 1.06e10 });
    expect(amzn!.forecast).toHaveLength(5);
    expect(amzn!.forecast![0]).toEqual({ year: 1, revenue: 7.4e11, growth: 0.09, ebitMargin: 0.104, fcf: 5.4e10 });
    expect([old!.bridge, old!.forecast, old!.terminalGrowth]).toEqual([null, null, null]);
    expect([part!.bridge, part!.forecast]).toEqual([null, null]);
  });

  it("badges nothing when pmcc has no config", () => {
    config = { paths: { technicalsDir: path.join(tmp, "technicals"), pmccConfigCandidates: [] } } as unknown as ConsoleConfig;
    write("2026-10-27.json", doc("2026-10-27", { AMZN: VALUED }));
    expect(readValuation(config, null).rows[0].pmcc).toBe(false);
  });
});
