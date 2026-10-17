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
    emMovePct: 4.75,
    suggestion: { kind: "credit", right: "P", short: 100, long: 95.25, expiry: "2026-11-20" },
    trade: null,
    listed: null,
    readSession: "2026-10-15",
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

  it("an unknown time is a dash, an estimated time and date say so, the move is the broker's", () => {
    const html = render("/screeners/earnings?day=2026-10-15");
    expect(html).toContain("No source gives the time of day");
    expect(html).toMatch(/title="Not confirmed: no source gives this date's time[^"]*">BMO est\.</);
    expect(html).toContain(">est.<");
    expect(html).toContain("4.8% <span class=\"muted\">(±$4.75)</span>");
  });

  it("a spread read as thin is hidden by default, one read as liquid shows its listed strikes and price", () => {
    const leg = (role: "long" | "short", strike: number, passes: boolean) => ({
      role,
      strike,
      bid: 1,
      ask: 1.1,
      openInterest: 500,
      volume: 50,
      passes,
    });
    const trade = (passes: boolean) => ({
      readingAt: "2026-10-19T15:30:00Z",
      kind: "credit" as const,
      right: "P" as const,
      expiry: "2026-11-20",
      width: 5,
      legs: [leg("long", 95, passes), leg("short", 100, true)],
      mid: 1.75,
      natural: 1.6,
      shareOfWidth: 0.35,
      maxLoss: 325,
      passes,
    });
    const saved = payload.rows;
    payload.rows = [row({ symbol: "GOOD", trade: trade(true) }), row({ symbol: "THIN", trade: trade(false) }), row({ symbol: "UNREAD" })];
    const html = render("/screeners/earnings?day=2026-10-15");
    expect(symbols(html)).toEqual(["GOOD", "UNREAD"]);
    expect(html).toContain("100/95");
    expect(html).toContain("1.75 cr");
    expect(symbols(render("/screeners/earnings?day=2026-10-15&trade=all"))).toEqual(["GOOD", "THIN", "UNREAD"]);
    expect(render("/screeners/earnings?day=2026-10-15&trade=all")).toMatch(/>thin</);
    payload.rows = saved;
  });

  it("a target price never reads as a strike: targets are marked, listed strikes say they are not priced", () => {
    const saved = payload.rows;
    payload.rows = [row({ symbol: "TGT" }), row({ symbol: "LST", listed: { expiry: "2026-11-20", strikes: [95, 100] } })];
    const html = render("/screeners/earnings?day=2026-10-15");
    // TGT: credit put, target short 100, long 95.25 -- shown approximate, with a "target" label.
    expect(html).toMatch(/≈100\.00 \/ ≈95\.25<\/span>/);
    expect(html).toMatch(/<span class="chip" title="Target prices, not listed strikes">target</);
    // LST: listed strikes lead the cell, short first for a credit; no price until one is read.
    expect(html).toContain("100/95 P");
    expect(html).not.toContain("not priced");
    payload.rows = saved;
  });

  it("shows one read, ours, one IV rank, the broker's, and says when we hold no bars", () => {
    const saved = payload.rows;
    payload.rows = [row({ symbol: "READ" }), row({ symbol: "NONE", edge: null, edgeLabel: null, strength: null, readSession: null })];
    const html = render("/screeners/earnings?day=2026-10-15&dir=all");
    expect(html).toContain("Ours (Bearing), from our bars as of the Oct 15 close.");
    expect(html).toContain("We hold no bars for this name, so no read.");
    expect(html).toContain("The broker's IV rank");
    expect(html).not.toMatch(/ours differs|broker \d+%|ours: /);
    payload.rows = saved;
  });

  it("gives the spread's kind its own column, ahead of the strikes", () => {
    const saved = payload.rows;
    payload.rows = [
      row({ symbol: "CR", listed: { expiry: "2026-11-20", strikes: [95, 100] } }),
      row({ symbol: "DB", suggestion: { kind: "debit", right: "C", long: 100, short: 105, expiry: "2026-11-20" } }),
      row({ symbol: "NOS", suggestion: null }),
    ];
    const html = render("/screeners/earnings?day=2026-10-15&dir=all");
    expect(html).toContain(">Kind</th><th>Spread</th>");
    expect(html).toMatch(/<td style="white-space:nowrap"><span class="chip chip-credit">credit put<\/span><\/td><td[^>]*>/);
    expect(html).toMatch(/<td style="white-space:nowrap"><span class="chip chip-debit">debit call<\/span><\/td>/);
    // The chip is no longer inside the spread cell.
    expect(html).not.toMatch(/100\/95 P[^<]*<\/span>[^]*?<br\/><span class="chip chip-credit">/);
    payload.rows = saved;
  });

  it("puts the symbol and its company in one cell", () => {
    const html = render("/screeners/earnings?day=2026-10-15");
    expect(html).toMatch(/symbol=AAA"[^>]*>AAA<\/a>(<span[^]*?<\/span>)?<br\/><span class="muted">Alpha Inc\.<\/span>/);
    expect(html).not.toContain(">Company<");
  });

  it("always says the suggested spread has no edge", () => {
    expect(render("/screeners/earnings?day=2026-10-15")).toContain("The suggested spread has no edge in history.");
  });
});
