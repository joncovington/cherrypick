import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { IncomeListPayload, IncomeListRow } from "@console/shared";
import { sortRows } from "../src/components/FilterSort";
import { writePref } from "../src/lib/prefs";

/**
 * Our own income lists on the page: what the defaults hide, the order, and the standing note. The
 * page decides nothing -- every figure is the builder's -- so the tests are about the cuts.
 */

function row(over: Partial<IncomeListRow>): IncomeListRow {
  return {
    symbol: "AAA",
    type: "Stock",
    spot: 100,
    expiry: "2026-12-18",
    dte: 59,
    window: null,
    strike: 88,
    delta: -0.16,
    iv: 0.3,
    bid: 1.2,
    ask: 1.3,
    mid: 1.25,
    working: 1.225,
    premium: 122.5,
    returnFraction: 0.0139,
    annualFraction: 0.0861,
    distanceFraction: -0.12,
    openInterest: 900,
    volume: 40,
    passes: true,
    quiet: false,
    earnings: "clear",
    earningsDate: "2027-01-28",
    earningsEstimated: false,
    exDividendInTrade: null,
    exDividend: null,
    exDividendDate: null,
    leveraged: null,
    fit: null,
    fair: null,
    ...over,
  };
}

let payload: IncomeListPayload;
vi.mock("../src/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/lib/api")>()),
  useIncomeList: () => ({ data: payload, isLoading: false, isError: false }),
}));

const { OwnListTab, IncomeListsTab, INCOME_LIST_SORTS, applyFilters, incomeHelp } = await import("../src/pages/Screeners/OwnListTab");

function render(url = "/screeners/short-puts", list: "short-puts" | "covered-calls" = "short-puts"): string {
  return renderToString(
    <MemoryRouter initialEntries={[url]}>
      <OwnListTab list={list} />
    </MemoryRouter>,
  ).replace(/<!-- -->/g, "");
}

/** The ? box's text for a list (it is drawn only while open, so the tests render its body). */
function helpText(list: "short-puts" | "covered-calls"): string {
  return renderToString(<>{incomeHelp(list, list === "covered-calls", []).body}</>)
    .replace(/<!-- -->/g, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ");
}

function base(rows: IncomeListRow[]): IncomeListPayload {
  return {
    list: "short-puts",
    day: "2026-10-20",
    days: ["2026-10-20"],
    builtAt: "b",
    chainTakenAt: "2026-10-20T18:45:00Z",
    chainComplete: true,
    chainProblem: null,
    names: rows.length,
    dropped: {},
    targetDelta: 0.16,
    rows,
    fitBasis: null,
  };
}

/** The names on the page, in order: each card's aria-label starts with its symbol. */
const symbols = (html: string) => [...html.matchAll(/aria-label="([A-Z0-9]+): (?:short put|covered call)/g)].map((m) => m[1]);

describe("our short-put list", () => {
  it("hides what does not fit the account once manage has judged it, with an escape", () => {
    payload = {
      ...base([row({ symbol: "FITS", fit: true }), row({ symbol: "BIGG", fit: false }), row({ symbol: "UNJD", fit: null })]),
      fitBasis: { session: "2026-10-20", availableBp: 8051.34, basis: "cash" },
    };
    const html = render();
    expect(html).toContain(">fits<"); // the summary says the filter is on
    expect(symbols(html)).toEqual(["FITS", "UNJD"]); // unjudged stays shown
    expect(html).toContain("1 that do not fit the account hidden");
    const everything = render("/screeners/short-puts?fit=all");
    expect(symbols(everything)).toContain("BIGG");
    expect(everything).toContain(">does not fit<");
  });

  it("hides a premium under the slider's floor, keeps an unquoted one, and is off by default", () => {
    payload = base([
      row({ symbol: "BIGP", premium: 122.5 }),
      row({ symbol: "SMLP", premium: 18 }),
      row({ symbol: "NOQT", premium: null, passes: null }),
    ]);
    const off = render();
    expect(symbols(off)).toEqual(["BIGP", "NOQT", "SMLP"]);
    expect(off).toContain(">any premium<");
    const html = render("/screeners/short-puts?premium=25");
    expect(symbols(html)).toEqual(["BIGP", "NOQT"]); // no premium to compare: shown, as under the other filters
    expect(html).toContain("1 under $25 hidden");
    // A value between stops reads as the stop below it, so the slider and the URL always agree.
    expect(render("/screeners/short-puts?premium=60")).toContain(">≥ $50<");
    expect(render("/screeners/short-puts?premium=junk")).toContain(">any premium<");
  });

  it("counts every hidden row once, so the parts add up to all less shown", () => {
    // Every combination of the four filters failing, plus a symbol search: rows failing two or more
    // used to count nowhere.
    const rows: IncomeListRow[] = [];
    for (let m = 0; m < 16; m++) {
      rows.push(
        row({
          symbol: `S${m}`,
          passes: m & 1 ? false : true,
          earnings: m & 2 ? "in-trade" : "clear",
          fit: m & 4 ? false : true,
          premium: m & 8 ? 10 : 200,
        }),
      );
    }
    for (const q of ["", "S1"]) {
      for (const minPremium of [0, 25]) {
        for (const allTrades of [false, true]) {
          const { shown, hidden } = applyFilters(rows, { allTrades, allEarnings: false, allFit: false, minPremium, q });
          const parts = hidden.thin + hidden.inTrade + hidden.unfit + hidden.small + hidden.unmatched;
          expect(parts).toBe(rows.length - shown.length);
        }
      }
    }
    const { hidden } = applyFilters(rows, { allTrades: false, allEarnings: false, allFit: false, minPremium: 25, q: "" });
    expect(hidden).toEqual({ thin: 8, inTrade: 4, unfit: 2, small: 1, unmatched: 0 });
    payload = { ...base(rows), fitBasis: { session: "2026-10-20", availableBp: 1000, basis: "cash" } };
    const html = render("/screeners/short-puts?premium=25");
    expect(html).toContain("1 of 16 names · 8 thin · 4 with earnings in the trade · 2 that do not fit the account · 1 under $25 hidden");
    // The "counted once" line is explanation: behind the ?.
    expect(html).not.toContain("counted once");
    expect(helpText("short-puts")).toContain("counted once");
  });

  it("says plainly when no call fits because the account holds no uncovered lot", () => {
    payload = {
      ...base([row({ symbol: "AAA", fit: false, delta: 0.16, strike: 110 }), row({ symbol: "BBB", fit: false, delta: 0.16, strike: 60 })]),
      list: "covered-calls",
      fitBasis: { session: "2026-10-20", availableBp: 1000, basis: "cash" },
    };
    const html = render("/screeners/covered-calls", "covered-calls");
    expect(html).toContain("The account holds no uncovered 100-share lot, so no call fits");
    expect(html).not.toContain("Nothing matches these filters");
    const all = render("/screeners/covered-calls?fit=all", "covered-calls");
    expect(all).not.toContain("so no call fits");
    expect(symbols(all)).toContain("AAA");
  });

  it("reads a call's ex-dividend date with its state, never a bare dash", () => {
    payload = {
      ...base([
        row({ symbol: "INTR", exDividend: "in-trade", exDividendDate: "2026-11-10", exDividendInTrade: true }),
        row({ symbol: "AFTR", exDividend: "after", exDividendDate: "2026-12-30", exDividendInTrade: false }),
        row({ symbol: "PAST", exDividend: "past", exDividendDate: "2026-08-17", exDividendInTrade: false }),
        row({ symbol: "NONE", exDividend: "unknown", exDividendDate: null, exDividendInTrade: false }),
        row({ symbol: "OLDF", exDividendInTrade: false }), // a list built before the state: unknown
        row({ symbol: "OLDT", exDividendInTrade: true }),
      ]),
      list: "covered-calls",
    };
    const html = render("/screeners/covered-calls", "covered-calls");
    expect(html).toContain("ex-dividend in the trade · Nov 10");
    expect(html).toContain("ex-dividend after expiry · Dec 30");
    expect(html).toContain("ex-dividend past · Aug 17");
    expect(html.match(/ex-dividend unknown/g)?.length).toBe(2);
    expect(html.match(/ex-dividend in the trade/g)?.length).toBe(2); // INTR and OLDT
    // A put says nothing of dividends.
    payload = base([row({ symbol: "PUTT" })]);
    expect(render()).not.toContain("ex-dividend");
  });

  it("offers no fit filter when no evaluation judged the session", () => {
    payload = base([row({ symbol: "AAA" })]);
    const html = render();
    expect(html).not.toContain(">fits<");
    expect(html).not.toContain(">does not fit<");
  });

  it("hides a thin option and a trade with earnings inside it, each with an escape", () => {
    payload = base([
      row({ symbol: "KEEP" }),
      row({ symbol: "THIN", passes: false }),
      row({ symbol: "ERNS", earnings: "in-trade", earningsDate: "2026-11-05" }),
      row({ symbol: "UNKN", earnings: "unknown", earningsDate: null }),
    ]);
    const html = render();
    expect(symbols(html)).toEqual(["KEEP", "UNKN"]); // an unknown date stays shown, marked
    expect(html).toContain("earnings date unknown");
    expect(html).toContain("2 of 4 names");
    const everything = render("/screeners/short-puts?trade=all&earnings=all");
    expect(symbols(everything)).toEqual(["ERNS", "KEEP", "THIN", "UNKN"]);
    expect(everything).toContain(">thin<");
    expect(everything).toContain("earnings in the trade · Nov 5");
  });

  it("badges a declared daily-reset fund and keeps it listed", () => {
    payload = base([row({ symbol: "TQQQ", type: "Exchange Traded Fund", leveraged: "leveraged" }), row({ symbol: "AAA" })]);
    const html = render();
    expect(symbols(html)).toEqual(["AAA", "TQQQ"]);
    expect(html).toMatch(/class="chip"[^>]*>leveraged</);
    expect(html.match(/>leveraged</g)?.length).toBe(1); // AAA carries no badge
  });

  it("keeps the builder's symbol order, never a yield order", () => {
    payload = base([row({ symbol: "AAA", annualFraction: 0.01 }), row({ symbol: "ZZZ", annualFraction: 0.5 })]);
    expect(symbols(render())).toEqual(["AAA", "ZZZ"]);
    // The sorts on offer are name, timing, distance and delta: none on credit or yield.
    expect(INCOME_LIST_SORTS.map((s) => s.id)).toEqual(["symbol", "expiry", "distance", "delta"]);
    const rows = [row({ symbol: "NEAR", distanceFraction: -0.05 }), row({ symbol: "FAR", distanceFraction: -0.2 })];
    expect(sortRows(rows, INCOME_LIST_SORTS.find((s) => s.id === "distance"), false).map((r) => r.symbol)).toEqual(["FAR", "NEAR"]);
  });

  it("flags the rule as untested in amber, rows or none, with its text behind the ?", () => {
    payload = base([row({})]);
    const html = render();
    expect(html).toContain('<button type="button" class="help-flag" title="Open the ? for the full text">no tested edge</button>');
    expect(html).toContain('aria-label="About short puts"');
    expect(html).not.toContain("In the history study");
    payload = base([]);
    expect(render()).toContain(">no tested edge<");
    expect(helpText("short-puts")).toContain("In the history study (52 names");
    expect(helpText("short-puts")).toContain("No tested edge");
    expect(helpText("covered-calls")).toContain("Not refuted, not an edge");
    payload = { ...base([row({})]), list: "covered-calls" };
    expect(render("/screeners/covered-calls", "covered-calls")).toContain(">not refuted, not an edge<");
  });

  it("shows the fair-value gap in the card's foot, states a non-valued name, and never orders by it", () => {
    // The builder's order (by symbol): the cheap name after the rich one, whatever the gap says.
    const rows = [
      row({ symbol: "AAA", fair: { fairValue: 80, fairLow: 60, fairHigh: 95, fairGap: 0.25, fairState: "valued", fairFlags: ["terminal_heavy"] } }),
      row({ symbol: "FUND", fair: { fairValue: null, fairLow: null, fairHigh: null, fairGap: null, fairState: "not-applicable", fairFlags: [] } }),
      row({ symbol: "OLD", fair: null }),
      row({ symbol: "ZZZ", fair: { fairValue: 150, fairLow: 120, fairHigh: 180, fairGap: -0.3333, fairState: "valued", fairFlags: [] } }),
    ];
    payload = base(rows);
    const html = render();
    expect(symbols(html)).toEqual(["AAA", "FUND", "OLD", "ZZZ"]);
    expect(html).toContain("vs fair +25.0% †");
    expect(html).toContain("vs fair −33.3%");
    expect(html).toContain("a fund, a bank or a REIT: no fair value");
    expect(html).toContain("no fair value");
    expect(helpText("short-puts")).toContain("tested 2018–26: no edge");
  });

  it("says when nothing has been built, and when the reading was cut short", () => {
    payload = { ...base([]), day: null, days: [] };
    expect(render()).toContain("No list built yet");
    payload = { ...base([row({})]), chainComplete: false, chainProblem: "chain listings stopped" };
    expect(render()).toContain("did not finish (chain listings stopped)");
  });
});

describe("the put or call card", () => {
  it("is a trade card by default: the credit for the contract, the order that opens it, the quote and the foot", () => {
    payload = base([row({ symbol: "AAA" })]);
    const html = render();
    expect(html).toContain('class="pos-card tc-card"');
    expect(html).toContain("tc-grid");
    expect(html).not.toContain("tr-list");
    expect(html).not.toContain("<table");
    expect(html).toContain("+$122.50"); // whole contract, a credit +
    expect(html).toContain("1.23 cr at the working price"); // the one per-share figure says so
    expect(html).toContain("1 2026-12-18 88P @ 1.23");
    expect(html).toContain("is-sell");
    expect(html).toContain("1.20 / 1.30 · Δ 0.16 · OI 900");
    expect(html).toContain("distance −12.0% to the strike");
    expect(html).toContain("annual 8.6%");
    expect(html).toContain("earnings after expiry · Jan 28");
  });

  it("puts the two lists in the card head as tabs, and a call's card says covered call", () => {
    payload = base([row({ symbol: "AAA" })]);
    const html = renderToString(
      <MemoryRouter initialEntries={["/screeners/income?list=calls"]}>
        <IncomeListsTab />
      </MemoryRouter>,
    ).replace(/<!-- -->/g, "");
    expect(html).toContain('aria-label="income list"');
    expect(html).toContain(">short puts<");
    expect(html).toContain(">covered calls<");
    expect(html).toMatch(/class="mode-btn active"[^>]*>covered calls</);
    expect(html).toContain("covered call 88C · Δ 0.16 · 59 d");
    expect(html).toContain("1 2026-12-18 88C @ 1.23");
  });

  it("draws rows once rows are chosen", () => {
    payload = base([row({ symbol: "AAA" }), row({ symbol: "BBB" })]);
    void writePref("view.income-short-puts", "rows");
    try {
      const html = render();
      expect(html).toContain("tr-list");
      expect(html).not.toContain("tc-grid");
      expect(html.match(/class="tr-row"/g)).toHaveLength(2);
      // The row names the strike, its delta, the days and the expiry, and never "proposed".
      expect(html).toContain("short put 88P · Δ 0.16 · 59 d · 18 Dec<");
      expect(html).not.toMatch(/class="tr-shape">[^<]*proposed/);
    } finally {
      void writePref("view.income-short-puts", undefined);
    }
  });
});
