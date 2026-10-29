import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ValuationModel, ValuationPayload, ValuationRow } from "@console/shared";

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
    terminalGrowth: 0.02,
    forecast: null,
    bridge: null,
    model: null,
    flags: [],
    pmcc: false,
    ...over,
  };
}

const RULE: ValuationPayload["rule"] = {
  forecastYears: 5,
  terminalGrowth: 0.02,
  riskFree: 0.04,
  riskFreeSource: "treasury 2026-10-26",
  equityPremium: 0.05,
  beta: "OLS",
  edge: "none measured",
};

let payload: ValuationPayload;
vi.mock("../src/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/lib/api")>()),
  useValuation: () => ({ data: payload, isLoading: false, isError: false }),
}));

const { ValuationTab, ValuationDetail, applyView, riskOrder, stretch } = await import("../src/pages/Screeners/ValuationTab");

function detail(r: ValuationRow): string {
  return renderToString(<ValuationDetail r={r} rule={RULE} />).replace(/<!-- -->/g, "");
}

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
    rule: RULE,
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

  it("shows the valued names by default, never ordered by the gap, with the gap and range kept to the details", () => {
    // Neither row has a reach or a stretch, so they fall to symbol order, whatever the gaps say.
    const aaa = row({ symbol: "AAA", gap: 0.25, fairValue: 80, low: 60, high: 95 });
    payload = base([aaa, row({ symbol: "ZZZ", gap: -0.5 })]);
    const html = render();
    expect(html.indexOf(">AAA<")).toBeLessThan(html.indexOf(">ZZZ<"));
    expect(html).toContain("80.00");
    expect(html).not.toContain("+25.0%"); // a row carries what the choice needs; the rest opens beneath it
    expect(html).not.toContain("WACC");
    expect(html).toContain("Show AAA&#x27;s details");
    const d = detail(aaa);
    expect(d).toContain("−20.0%"); // the model's value against the price: 80 / 100 − 1
    expect(d).toContain("Low $60.00");
    expect(d).toContain("High $95.00");
    expect(d).toContain("Price $100.00");
    expect(d).toContain("Discount rate (WACC)");
  });

  it("captions the value-vs-price dial with the no-edge result, and names the stretch dial's zones in words", () => {
    const inside = detail(row({ reach: "within history", impliedMargin: 0.1, bestMargin: 0.2 }));
    expect(inside).toContain("Model value vs price");
    expect(inside).toMatch(/Tested 2018–26: no edge<\/strong> as a way to pick names/);
    expect(inside).toContain("0.50×");
    expect(inside).toContain("inside its record");
    expect(detail(row({ reach: "beyond history", impliedMargin: 0.3, bestMargin: 0.2 }))).toContain("beyond its record");
  });

  it("opens with what the price needs, and says when it needs nothing new", () => {
    const below = detail(row({ spot: 100, fairValue: 120, reach: "below value" }));
    expect(below).toContain("The model&#x27;s value ($120.00) is 20% above today&#x27;s price ($100.00)");
    expect(below).toContain("asks for nothing the business has not already done");
    const beyond = detail(row({ spot: 100, fairValue: 50, ebitMargin: 0.1, impliedMargin: null, bestMargin: 0.12, reach: "beyond history" }));
    expect(beyond).toContain("Beyond the company&#x27;s record.");
    expect(beyond).toContain("no operating margin under 100% of revenue gets there");
  });

  it("walks from the business's value to the value per share, and draws the forecast's years", () => {
    const r = row({
      fairValue: 33.8,
      bridge: { pvForecast: 1.59e11, pvTerminal: 2.35e11, ev: 3.94e11, debt: 1.75e11, cash: 1.08e10, equity: 2.29e11, shares: 6.77e9, financeLease: 0, preferred: 0, nci: 0, basicShares: 6.77e9, rsu: 0, optionShares: 0 },
      forecast: [1, 2, 3, 4, 5].map((year) => ({ year, revenue: 1.2e11, growth: 0.03, ebitMargin: 0.187, fcf: 1.9e10 + year * 1e9, growthSource: null, ebit: null, nopat: null, da: null, capex: null, wcChange: null, discount: null, pv: null })),
    });
    const d = detail(r);
    expect(d).toContain("= Enterprise value");
    expect(d).toContain("$394B");
    expect(d).toContain("−$175B"); // debt comes off
    expect(d).toContain("÷ 6.77B shares");
    expect(d).toContain("$33.80/share");
    expect(d).toContain("Y1 growth: analyst consensus");
    expect(d.match(/<rect /g)?.length).toBe(5);
    expect(d).toContain("$24.0B"); // the last year, labelled
    expect(d).toContain("Risk-free rate (treasury 2026-10-26)");
    expect(detail(row({}))).toContain("Not in this file (built before 2026-10-28).");
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

  it("counts the builder's flags on the row and spells them out, with the broker's beta, in the details", () => {
    const r = row({ flags: ["terminal_heavy", "beta_disagrees"], betaBroker: 1.9 });
    payload = base([r]);
    expect(render()).toContain("2 notes");
    const d = detail(r);
    expect(d).toContain("Over 85% of the value is the perpetuity");
    expect(d).toContain("broker 1.90");
  });

  it("shows the reach in words on the row, and what the price needs beside the company's best in the details", () => {
    const aaa = row({ symbol: "AAA", fairValue: 60, impliedMargin: 0.407, bestMargin: 0.112, impliedGrowth: 2, impliedGrowthEdge: "above", bestGrowth: 0.124, reach: "beyond history" });
    payload = base([aaa, row({ symbol: "BBB", impliedMargin: 0.12, bestMargin: 0.15, impliedGrowth: 0.05, bestGrowth: 0.08, reach: "within history" })]);
    const html = render();
    expect(html).toContain("beyond history");
    expect(html).toContain("within history");
    expect(html).toContain("the price assumes a better business than the record shows");
    expect(html).toContain("3.63×"); // 0.407 / 0.112
    const d = detail(aaa);
    expect(d).toContain("To match the price");
    expect(d).toContain("40.7%");
    expect(d).toContain("its best in five years was 11.2%");
    expect(d).toContain("over +200.0%");
  });

  it("lists every input with its source in the Advanced view, and only for a version-2 row", () => {
    const model: ValuationModel = {
      capital: {
        rf: 0.0499, rfSource: "10-year Treasury 2026-10-27", erp: 0.042, erpMonth: "2026-10-01", betaRaw: 0.24, beta: 0.49,
        ke: 0.0706, coverage: 7.9, rating: "Aa2/AA", spread: 0.0055, ratingsAsOf: "January 2026", kdPreTax: 0.0554,
        kdAfterTax: 0.0415, marginalTax: 0.25, equityWeight: 0.742, debtWeight: 0.258, wacc: 0.0631,
      },
      drivers: { ebitMargin: 0.2, daPct: 0.14, capexPct: 0.027, wcPct: 0.009, wcIsLevel: true, taxRate: 0.156, historyYears: 5 },
      terminal: { fcf: 2.94e9, value: 7.71e10, reinvestmentRate: 0.396, roic: 0.0631 },
      consensus: { snapshot: "2026-10-25", years: [{ periodEnd: "2026-12-31", revenue: 4.99e10, analysts: 9, high: 5.01e10, low: 4.91e10 }] },
      history: [{ end: "2025-12-31", revenue: 4.82e10, ebitMargin: 0.233, daPct: 0.086, capexPct: 0.027, wcPct: -0.024, taxRate: 0.244 }],
      grid: [],
      data: { fiscalYearEnd: "2025-12-31", filed: "2026-02-11", latestPeriod: "2026-06-30", latestFiled: "2026-07-30", sic: "2834", sicDescription: "Pharmaceutical Preparations", source: "SEC filings" },
      sources: { revenue: "Revenues", interest: "InterestExpenseNonoperating", capex: "Dolt (not stated in the filing)", ebit: "derived: pretax + interest" },
      disagree: { interest: [1.89e9, 0] },
      impliedWacc: 0.081,
    };  // fmt: skip
    const d = detail(row({ model }));
    expect(d).toContain("Advanced: every input and where it came from");
    expect(d).toContain("SEC filing of 2026-02-11: Revenues");
    expect(d).toContain("SEC filing of 2026-02-11: InterestExpenseNonoperating");
    expect(d).toContain("Dolt annual statements (the filing does not state it)");
    expect(d).toContain("derived from the filing: pretax + interest");
    expect(d).toContain("Damodaran implied ERP, 2026-10-01");
    expect(d).toContain("Aa2/AA synthetic rating from interest coverage (Damodaran&#x27;s table, January 2026)");
    expect(d).toContain("9 analysts");
    expect(d).toContain("Year-by-year calculation");
    expect(d).toContain("interest: the filing states $1.9B, Dolt $0; the value uses the filing.");
    expect(detail(row({}))).not.toContain("Advanced:");
  });

  it("says in words, in the details, why a name has no value", () => {
    expect(detail(row({ state: "not-applicable", reason: "financials" }))).toContain("A bank earns on its balance sheet");
    const debt = detail(row({ state: "refused", reason: "debt_dominated", fairValue: null }));
    expect(debt).toContain("No fair value.");
    expect(debt).toContain("covers its interest less than 1.5 times");
    expect(detail(row({ state: "refused", reason: "something_new" }))).toContain("something new.");
  });

  it("says when nothing has been built", () => {
    payload = { ...base([]), day: null, days: [] };
    expect(render()).toContain("No fair-value file built yet");
  });
});
