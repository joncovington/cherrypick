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
    leveraged: null,
    ...over,
  };
}

let payload: IncomeListPayload;
vi.mock("../src/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/lib/api")>()),
  useIncomeList: () => ({ data: payload, isLoading: false, isError: false }),
}));

const { OwnListTab } = await import("../src/pages/Screeners/OwnListTab");

function render(url = "/screeners/short-puts"): string {
  return renderToString(
    <MemoryRouter initialEntries={[url]}>
      <OwnListTab list="short-puts" />
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
  };
}

describe("our short-put list", () => {
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

  it("says when nothing has been built, and when the reading was cut short", () => {
    payload = { ...base([]), day: null, days: [] };
    expect(render()).toContain("No list built yet");
    payload = { ...base([row({})]), chainComplete: false, chainProblem: "chain listings stopped" };
    expect(render()).toContain("did not finish (chain listings stopped)");
  });
});
