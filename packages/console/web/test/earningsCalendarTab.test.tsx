import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { EarningsCalendarPayload, EarningsCalendarRow } from "@console/shared";

/**
 * The earnings tab: what its defaults keep (the vendor's view: strong reads, very liquid, optionable,
 * US, a week), how rows group by day, and that the no-edge note is always there. It decides nothing,
 * so the tests are about the cuts and the cells. Dates are pinned to an older calendar, whose windows
 * count from its own session rather than today.
 */

function row(over: Partial<EarningsCalendarRow>): EarningsCalendarRow {
  return {
    symbol: "AAA",
    company: "Alpha Inc.",
    date: "2026-10-20",
    timing: "amc",
    timingSource: "broker",
    usualTiming: null,
    dateSource: "broker",
    dateConfirmed: true,
    dateGapDays: 0,
    entrySession: "2026-10-20",
    reactionDay: "2026-10-21",
    type: "Stock",
    region: "US",
    edge: "sb",
    edgeLabel: "strong bull",
    strength: 9,
    ivRank: 67,
    liquidityRank: 1,
    optionable: true,
    sector: "Financials",
    price: 100,
    eventExpiry: "2026-10-23",
    eventIv: 0.5,
    sdMove: 7,
    sdMovePct: 7,
    emMove: 4.75,
    suggestion: { kind: "credit", right: "P", short: 100, long: 95.25, expiry: "2026-11-20" },
    ...over,
  };
}

const ROWS = [
  row({}),
  row({ symbol: "BBB", date: "2026-10-21", timing: "bmo", edge: "sbe", edgeLabel: "strong bear" }),
  row({ symbol: "MIX", edge: "bp", edgeLabel: "pullback", suggestion: null }),
  row({ symbol: "THIN", liquidityRank: 2 }),
  row({ symbol: "SAP", region: "intl", type: "Depository Receipt" }),
  row({ symbol: "LATER", date: "2026-11-05" }),
  row({ symbol: "UNK", timing: null, entrySession: null, dateConfirmed: false, dateSource: "broker" }),
  row({ symbol: "EST", date: "2026-10-21", timing: null, timingSource: null, usualTiming: "bmo", entrySession: null }),
];

const payload: EarningsCalendarPayload = {
  day: "2026-10-15",
  days: ["2026-10-16", "2026-10-15"],
  inputs: { securityList: "2026-10-15", expiryIv: "2026-10-15", brokerDates: "2026-10-15", nasdaqTimes: "2026-10-15", closeDate: "2026-10-15" },
  ivRankCut: 50,
  rows: ROWS,
};

vi.mock("../src/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/lib/api")>()),
  useEarningsCalendar: () => ({ data: payload, isLoading: false, isError: false }),
}));

const { EarningsCalendarTab } = await import("../src/pages/Screeners/EarningsCalendarTab");

function render(url: string): string {
  return renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[url]}>
        <EarningsCalendarTab />
      </MemoryRouter>
    </QueryClientProvider>,
  )
    .replace(/<!-- -->/g, "")
    .replace(/&#x27;/g, "'");
}

const symbols = (html: string) => [...html.matchAll(/symbol=([A-Z]+)"/g)].map((m) => m[1]);

describe("the earnings tab", () => {
  it("keeps the vendor's default view: strong reads, very liquid, optionable, US, the next week", () => {
    expect(symbols(render("/screeners/earnings?day=2026-10-15"))).toEqual(["AAA", "UNK", "BBB", "EST"]);
  });

  it("each cut has its escape", () => {
    expect(symbols(render("/screeners/earnings?day=2026-10-15&dir=all"))).toContain("MIX");
    expect(symbols(render("/screeners/earnings?day=2026-10-15&liq=1,2"))).toContain("THIN");
    expect(symbols(render("/screeners/earnings?day=2026-10-15&region=all"))).toContain("SAP");
    expect(symbols(render("/screeners/earnings?day=2026-10-15&win=30"))).toContain("LATER");
    // An estimated time counts under its filter, as the vendor's calendar shows one for every row.
    expect(symbols(render("/screeners/earnings?day=2026-10-15&time=bmo"))).toEqual(["BBB", "EST"]);
  });

  it("groups by day under a heading with its count", () => {
    const html = render("/screeners/earnings?day=2026-10-15");
    expect(html).toMatch(/Tuesday, Oct 20<\/strong> <span class="chip">2</);
    expect(html).toMatch(/Wednesday, Oct 21<\/strong> <span class="chip">2</);
  });

  it("an unknown time is a dash, an estimated time and date say so, the move is one SD", () => {
    const html = render("/screeners/earnings?day=2026-10-15");
    expect(html).toContain("No source gives the time of day");
    expect(html).toMatch(/title="Not confirmed: no source gives this date's time[^"]*">BMO est\.</);
    expect(html).toContain(">est.<");
    expect(html).toContain("±7.0%");
  });

  it("always says the suggested spread has no edge", () => {
    expect(render("/screeners/earnings?day=2026-10-15")).toContain("The suggested spread has no edge in history.");
  });
});
