import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ValuationPayload, ValuationRow } from "@console/shared";

/**
 * The fair-value tab: the standing no-edge note, the views, the symbol order, the pmcc badge and
 * what a non-valued row shows. The page decides nothing -- every figure is the builder's.
 */

function row(over: Partial<ValuationRow>): ValuationRow {
  return {
    symbol: "AAA",
    type: "Stock",
    sector: "Technology",
    state: "valued",
    reason: null,
    spot: 100,
    spotDate: "2026-10-27",
    fiscalYearEnd: "2025-12-31",
    fairValue: 120,
    low: 100,
    high: 140,
    gap: -0.1667,
    wacc: 0.09,
    beta: 1.1,
    betaRaw: 1.1,
    betaBroker: null,
    marketCap: 5e9,
    marketCapBroker: null,
    growthY1: 0.08,
    ebitMargin: 0.15,
    terminalShare: 0.68,
    impliedEvEbitda: 12.3,
    impliedMargin: null,
    impliedGrowth: null,
    impliedGrowthEdge: null,
    bestMargin: null,
    bestGrowth: null,
    reach: null,
    consensusUsed: true,
    flags: [],
    pmcc: false,
    ...over,
  };
}

let payload: ValuationPayload;
vi.mock("../src/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/lib/api")>()),
  useValuation: () => ({ data: payload, isLoading: false, isError: false }),
}));

const { ValuationTab, applyView, riskOrder, stretch } = await import("../src/pages/Screeners/ValuationTab");

function render(url = "/screeners/valuation"): string {
  return renderToString(
    <MemoryRouter initialEntries={[url]}>
      <ValuationTab />
    </MemoryRouter>,
  ).replace(/<!-- -->/g, "");
}

function base(rows: ValuationRow[]): ValuationPayload {
  return {
    day: "2026-10-27",
    days: ["2026-10-27"],
    builtAt: "b",
    universe: "public list of 2026-10-27",
    names: rows.length,
    states: { valued: rows.filter((r) => r.state === "valued").length },
    rule: { forecastYears: 5, terminalGrowth: 0.02, riskFree: 0.04, equityPremium: 0.05, beta: "OLS", edge: "none measured" },
    rows,
  };
}

describe("the fair-value tab", () => {
  it("always says the estimate was tested and has no edge", () => {
    payload = base([row({})]);
    expect(render()).toContain("Tested 2018–26: no edge.");
    payload = base([]);
    expect(render()).toContain("Tested 2018–26: no edge.");
  });

  it("shows the valued names by default, with the gap and the range, never ordered by the gap", () => {
    // Neither row has a reach or a stretch, so they fall to symbol order, whatever the gaps say.
    payload = base([row({ symbol: "AAA", gap: 0.25, fairValue: 80, low: 60, high: 95 }), row({ symbol: "ZZZ", gap: -0.5 })]);
    const html = render();
    expect(html.indexOf(">AAA<")).toBeLessThan(html.indexOf(">ZZZ<"));
    expect(html).toContain("+25.0%");
    expect(html).toContain("60.00 – 95.00");
  });

  it("opens by what the price asks: reach, then stretch, then symbol", () => {
    const rows = [
      row({ symbol: "BEY", reach: "beyond history", impliedMargin: 0.3, bestMargin: 0.2 }), // 1.5
      row({ symbol: "NON", reach: "beyond history", impliedMargin: null, bestMargin: 0.2 }), // none reaches
      row({ symbol: "WIB", reach: "within history", impliedMargin: 0.18, bestMargin: 0.2 }), // 0.9
      row({ symbol: "WIA", reach: "within history", impliedMargin: 0.1, bestMargin: 0.2, gap: 0.9 }), // 0.5
      row({ symbol: "BLW", reach: "below value", impliedMargin: 0.12, bestMargin: 0.2, gap: 0.4 }), // 0.6
      row({ symbol: "AAA" }), // no reach: last
    ];
    expect(riskOrder(rows).map((r) => r.symbol)).toEqual(["BLW", "WIA", "WIB", "BEY", "NON", "AAA"]);
    expect(stretch(rows[0]!)).toBeCloseTo(1.5);
    expect(stretch(rows[1]!)).toBe(Number.POSITIVE_INFINITY);
    expect(stretch(row({ state: "refused", bestMargin: 0.2, impliedMargin: 0.1 }))).toBeNull();
    payload = base(rows);
    const html = render();
    expect(html.indexOf(">BLW<")).toBeLessThan(html.indexOf(">WIA<"));
    expect(html.indexOf(">WIB<")).toBeLessThan(html.indexOf(">BEY<"));
    expect(html).toContain("1.50×");
    expect(html).toContain("none reaches");
  });

  it("hides a non-valued name until the All view, which says why there is no value", () => {
    payload = base([row({}), row({ symbol: "BNK", state: "not-applicable", reason: "financials", fairValue: null, gap: null })]);
    expect(render()).not.toContain("BNK");
    const all = render("/screeners/valuation?view=all");
    expect(all).toContain("BNK");
    expect(all).toContain("not applicable");
    expect(all).toContain("financials");
  });

  it("badges pmcc's symbols and the pmcc view keeps them whether valued or not", () => {
    const rows = [row({ symbol: "AMZN", pmcc: true }), row({ symbol: "XSP", type: "Exchange Traded Fund", state: "not-applicable", reason: "fund", pmcc: true, fairValue: null, gap: null }), row({})];
    payload = base(rows);
    const html = render("/screeners/valuation?view=pmcc");
    expect(html).toContain("AMZN");
    expect(html).toContain("XSP");
    expect(html).not.toMatch(/>AAA</);
    expect(html.match(/A symbol pmcc trades/g)?.length).toBe(2); // the badge, not the view button
    expect(applyView(rows, "pmcc", "").map((r) => r.symbol)).toEqual(["AMZN", "XSP"]);
    expect(applyView(rows, "valued", "A").map((r) => r.symbol)).toEqual(["AMZN", "AAA"]);
  });

  it("shows the builder's flags and the broker's beta where it disagrees", () => {
    payload = base([row({ flags: ["terminal_heavy", "beta_disagrees"], betaBroker: 1.9 })]);
    const html = render();
    expect(html).toContain("terminal heavy");
    expect(html).toContain("1.90");
  });

  it("shows what the price needs beside the company's best, and the builder's reach in words", () => {
    payload = base([
      row({ symbol: "AAA", impliedMargin: 0.407, bestMargin: 0.112, impliedGrowth: 2, impliedGrowthEdge: "above", bestGrowth: 0.124, reach: "beyond history" }),
      row({ symbol: "BBB", impliedMargin: 0.12, bestMargin: 0.15, impliedGrowth: 0.05, bestGrowth: 0.08, reach: "within history" }),
    ]);
    const html = render();
    expect(html).toContain("Needs margin");
    expect(html).toContain("40.7%");
    expect(html).toContain("best 11.2%");
    expect(html).toContain("over +200.0%");
    expect(html).toContain("beyond history");
    expect(html).toContain("within history");
    expect(html).toContain("the price assumes a better business than the record shows");
  });

  it("says when nothing has been built", () => {
    payload = { ...base([]), day: null, days: [] };
    expect(render()).toContain("No fair-value file built yet");
  });
});
