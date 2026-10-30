import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { IncomeListPayload, IncomeListRow } from "@console/shared";

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

const { OwnListTab, applyFilters, incomeColumns } = await import("../src/pages/Screeners/OwnListTab");
const { SecondaryCells, secondaryOf } = await import("../src/components/columns");

function render(url = "/screeners/short-puts", list: "short-puts" | "covered-calls" = "short-puts"): string {
  return renderToString(
    <MemoryRouter initialEntries={[url]}>
      <OwnListTab list={list} />
    </MemoryRouter>,
  ).replace(/<!-- -->/g, "");
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

describe("our short-put list", () => {
  it("hides what does not fit the account once manage has judged it, with an escape", () => {
    payload = {
      ...base([row({ symbol: "FITS", fit: true }), row({ symbol: "BIGG", fit: false }), row({ symbol: "UNJD", fit: null })]),
      fitBasis: { session: "2026-10-20", availableBp: 8051.34, basis: "cash" },
    };
    const html = render();
    expect(html).toContain("Fits the account");
    expect(html).toContain("FITS");
    expect(html).toContain("UNJD"); // unjudged stays shown
    expect(html).not.toContain("BIGG");
    expect(html).toContain("1 that do not fit the account hidden");
    const everything = render("/screeners/short-puts?fit=all");
    expect(everything).toContain("BIGG");
    expect(everything).toContain("does not fit");
  });

  it("hides a premium under the slider's floor, keeps an unquoted one, and is off by default", () => {
    payload = base([
      row({ symbol: "BIGP", premium: 122.5 }),
      row({ symbol: "SMLP", premium: 18 }),
      row({ symbol: "NOQT", premium: null, passes: null }),
    ]);
    const off = render();
    for (const s of ["BIGP", "SMLP", "NOQT"]) expect(off).toContain(s);
    expect(off).toContain("Premium ≥ any");
    const html = render("/screeners/short-puts?premium=25");
    expect(html).toContain("BIGP");
    expect(html).toContain("NOQT"); // no premium to compare: shown, as under the other filters
    expect(html).not.toContain("SMLP");
    expect(html).toContain("1 under $25 hidden");
    // A value between stops reads as the stop below it, so the slider and the URL always agree.
    expect(render("/screeners/short-puts?premium=60")).toContain("Premium ≥ $50");
    expect(render("/screeners/short-puts?premium=junk")).toContain("Premium ≥ any");
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
    expect(html).toContain("1 of 16 names · 8 thin hidden · 4 more with earnings in the trade hidden · 2 more that do not fit the account hidden · 1 more under $25 hidden");
    expect(html).toContain("counted once");
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
    expect(all).toContain("AAA");
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
    expect(html).toContain("in trade · Nov 10");
    expect(html).toContain("after · Dec 30");
    expect(html).toContain("past · Aug 17");
    expect(html.match(/>unknown</g)?.length).toBe(2);
    expect(html).toMatch(/>in trade</);
    expect(html).not.toMatch(/<td><span class="muted">—<\/span><\/td>/);
  });

  it("offers no fit filter when no evaluation judged the session", () => {
    payload = base([row({ symbol: "AAA" })]);
    expect(render()).not.toContain("Fits the account");
  });

  it("hides a thin option and a trade with earnings inside it, each with an escape", () => {
    payload = base([
      row({ symbol: "KEEP" }),
      row({ symbol: "THIN", passes: false }),
      row({ symbol: "ERNS", earnings: "in-trade", earningsDate: "2026-11-05" }),
      row({ symbol: "UNKN", earnings: "unknown", earningsDate: null }),
    ]);
    const html = render();
    expect(html).toContain("KEEP");
    expect(html).toContain("UNKN"); // an unknown date stays shown, marked
    expect(html).not.toContain("THIN");
    expect(html).not.toContain("ERNS");
    expect(html).toContain("2 of 4 names");
    const everything = render("/screeners/short-puts?trade=all&earnings=all");
    for (const s of ["KEEP", "THIN", "ERNS", "UNKN"]) expect(everything).toContain(s);
  });

  it("badges a declared daily-reset fund and keeps it listed", () => {
    payload = base([row({ symbol: "TQQQ", type: "Exchange Traded Fund", leveraged: "leveraged" }), row({ symbol: "AAA" })]);
    const html = render();
    expect(html).toContain("TQQQ");
    expect(html).toMatch(/class="chip"[^>]*>leveraged</);
    expect(html.match(/>leveraged</g)?.length).toBe(1); // AAA carries no badge
  });

  it("keeps the builder's symbol order, never a yield order", () => {
    payload = base([row({ symbol: "AAA", annualFraction: 0.01 }), row({ symbol: "ZZZ", annualFraction: 0.5 })]);
    const html = render();
    expect(html.indexOf("AAA")).toBeLessThan(html.indexOf("ZZZ"));
  });

  it("always says the rule has no tested edge", () => {
    payload = base([row({})]);
    expect(render()).toContain("No tested edge.");
    payload = base([]);
    expect(render()).toContain("No tested edge.");
  });

  it("shows the fair-value gap in the row's expanded line, states a non-valued name, and never orders by it", () => {
    // The builder's order (by symbol): the cheap name after the rich one, whatever the gap says.
    const rows = [
      row({ symbol: "AAA", fair: { fairValue: 80, fairLow: 60, fairHigh: 95, fairGap: 0.25, fairState: "valued", fairFlags: ["terminal_heavy"] } }),
      row({ symbol: "FUND", fair: { fairValue: null, fairLow: null, fairHigh: null, fairGap: null, fairState: "not-applicable", fairFlags: [] } }),
      row({ symbol: "OLD", fair: null }),
      row({ symbol: "ZZZ", fair: { fairValue: 150, fairLow: 120, fairHigh: 180, fairGap: -0.3333, fairState: "valued", fairFlags: [] } }),
    ];
    payload = base(rows);
    const html = render();
    // Out of the row since 2026-10-30 (docs/console-review-plan.md batch 3): a model opinion, not a pick.
    expect(html).not.toContain("Vs fair");
    expect(html.indexOf("AAA<")).toBeLessThan(html.indexOf("ZZZ<"));
    const line = (r: IncomeListRow) => renderToString(<SecondaryCells cols={secondaryOf(incomeColumns(false, null, () => {}))} row={r} />);
    expect(line(rows[0]!)).toContain("Vs fair");
    expect(line(rows[0]!)).toContain("+25.0%");
    expect(line(rows[3]!)).toContain("−33.3%");
    expect(line(rows[1]!)).toContain(">n/a<");
    expect(line(rows[0]!)).toContain("tested 2018–26: no edge");
  });

  it("says when nothing has been built, and when the reading was cut short", () => {
    payload = { ...base([]), day: null, days: [] };
    expect(render()).toContain("No list built yet");
    payload = { ...base([row({})]), chainComplete: false, chainProblem: "chain listings stopped" };
    expect(render()).toContain("did not finish (chain listings stopped)");
  });
});
