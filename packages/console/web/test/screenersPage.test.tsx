import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ScreenerPayload, ScreenerRow } from "@console/shared";

/**
 * The Screeners page: what its defaults hide, and how a row says which trade to take. It decides
 * nothing -- the server joins every figure -- so the tests are about the cuts and the cells.
 */

function row(over: Partial<ScreenerRow>): ScreenerRow {
  return {
    symbol: "AAA",
    direction: "bullish",
    right: "P",
    price: 100,
    ivRank: 41.5,
    liquid: true,
    expiry: "2026-11-13",
    dte: 30,
    weekly: true,
    strikes: [98],
    mid: 2,
    bid: 1.9,
    ask: 2.1,
    rawReturnPct: 2,
    annualisedPct: 24.33,
    distancePct: -2,
    width: null,
    creditPct: null,
    ours: [98],
    earningsDate: "2027-01-28",
    earningsSource: "broker",
    earningsEstimated: false,
    cycleExpiry: "2026-11-20",
    earningsExpiry: "2026-11-13",
    earnings: "clear",
    readingAt: "2026-10-15T18:30:00Z",
    listedQuote: null,
    listedLegs: [{ role: "short", right: "P", expiry: "2026-11-13", strike: 98, bid: 1.9, ask: 2.1, openInterest: 500, volume: 50, passes: true, quiet: false }],
    cycleLegs: [{ role: "short", right: "P", expiry: "2026-11-20", strike: 98, bid: 2.2, ask: 2.3, openInterest: 900, volume: 40, passes: true, quiet: false }],
    recommendation: "listed",
    cyclePasses: true,
    trade: {
      side: "listed",
      expiry: "2026-11-13",
      dte: 30,
      mid: 2,
      working: 1.95,
      natural: 1.9,
      cost: 0.1,
      workingAnnualisedPct: 23.72,
      naturalAnnualisedPct: 23.12,
      workingCreditPct: null,
    },
    ...over,
  };
}

const ROWS = [
  row({}),
  row({ symbol: "BBB", ours: [97], recommendation: "cycle", cyclePasses: false }),
  row({ symbol: "CCC", liquid: false }),
  row({ symbol: "DDD", earnings: "in-cycle", earningsDate: "2026-11-05" }),
  row({ symbol: "EEE", recommendation: null, ours: null }),
];

let payload: ScreenerPayload = {
  list: "short-puts",
  day: "2026-10-14",
  days: ["2026-10-14"],
  createdAt: "10/14/2026 03:49 PM",
  pricesAt: "10/14/2026 05:16 PM",
  scored: true,
  rows: ROWS,
};

vi.mock("../src/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/lib/api")>()),
  useScreener: () => ({ data: payload, isLoading: false, isError: false }),
}));

const { ScreenerTab } = await import("../src/pages/Screeners/ScreenersPage");

function render(url: string): string {
  return renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[url]}>
        <ScreenerTab list="short-puts" />
      </MemoryRouter>
    </QueryClientProvider>,
  )
    .replace(/<!-- -->/g, "")
    .replace(/&#x27;/g, "'");
}

const symbols = (html: string) => [...html.matchAll(/symbol=([A-Z]+)"/g)].map((m) => m[1]);

describe("the screener page", () => {
  it("shows tradable names with no earnings in the cycle and a liquid trade by default, and says what it hid", () => {
    const html = render("/screeners/short-puts");
    expect(symbols(html)).toEqual(["AAA", "EEE"]);
    expect(html).toContain(
      "2 of 5 names · 1 not tradable hidden · 1 with earnings in cycle hidden · 1 with no liquid trade hidden · 1 not yet read for liquidity",
    );
  });

  it("each cut has its escape", () => {
    expect(symbols(render("/screeners/short-puts?names=all"))).toEqual(["AAA", "CCC", "EEE"]);
    expect(symbols(render("/screeners/short-puts?earnings=all"))).toEqual(["AAA", "DDD", "EEE"]);
    expect(symbols(render("/screeners/short-puts?trade=all"))).toEqual(["AAA", "BBB", "EEE"]);
    expect(render("/screeners/short-puts?names=all")).toContain("· no opts");
  });

  it("no liquid trade means the listed option and the cycle monthly both fail, or no monthly is listed", () => {
    const saved = payload;
    payload = {
      ...payload,
      rows: [
        row({ symbol: "LST" }),
        row({ symbol: "CYC", recommendation: "cycle", cyclePasses: true }),
        row({ symbol: "THN", recommendation: "cycle", cyclePasses: false }),
        row({ symbol: "NON", recommendation: "none", cyclePasses: null, cycleLegs: null }),
        row({ symbol: "UNR", recommendation: null, cyclePasses: null }),
      ],
    };
    expect(symbols(render("/screeners/short-puts"))).toEqual(["LST", "CYC", "UNR"]);
    payload = saved;
  });

  it("the trade cell names the listed option, the cycle monthly (thin when it fails too), or no reading", () => {
    const html = render("/screeners/short-puts?trade=all");
    expect(html).toMatch(/title="The listed option is liquid enough to trade\.[^"]*">listed</);
    expect(html).toMatch(/Nov 20 98P <span class="chip chip-warn"[^>]*>thin</);
    expect(html).toContain(">no reading<");
  });

  it("our pick is a tick when it is the list's, our strike when not, a dash when unjudged", () => {
    const html = render("/screeners/short-puts?trade=all");
    expect(html).toMatch(/Our reproduced rule picks the same strike\(s\)">✓</);
    expect(html).toMatch(/where they differ from the list's">97</);
    expect(html).toMatch(/broker chain was not recorded for this name">—</);
  });

  it("the trade is priced at the working price, with mid and natural behind it", () => {
    const html = render("/screeners/short-puts");
    expect(html).toMatch(/mid 2\.00, working 1\.95, natural 1\.90[^"]*">1\.95</);
    expect(html).toMatch(/At the natural fill: 23\.1%\.">23\.7%</);
  });

  it("a row with no priced trade shows a dash, never a figure carried over from the list", () => {
    const saved = payload;
    payload = { ...payload, rows: [row({ symbol: "UNR", recommendation: null, trade: null })] };
    expect(render("/screeners/short-puts")).toMatch(/<td class="num muted">—<\/td><td class="num muted">—<\/td><\/tr>/);
    payload = saved;
  });

  it("a listed quote with no bid, or wider than its mid, says so instead of a yield", () => {
    const saved = payload;
    payload = {
      ...payload,
      rows: [
        row({ symbol: "NOB", bid: 0, ask: 4.9, listedQuote: "no-bid", annualisedPct: 32.79 }),
        row({ symbol: "WID", bid: 0.3, ask: 1.2, listedQuote: "wide" }),
      ],
    };
    const html = render("/screeners/short-puts");
    expect(html).toMatch(/has no bid[^"]*The list states 32\.8%\."><span class="chip chip-warn">no bid</);
    expect(html).toContain(">wide</span>");
    expect(html).not.toContain(">32.8%<");
    payload = saved;
  });

  it("an unscored list says when its picks arrive", () => {
    payload = { ...payload, scored: false };
    expect(render("/screeners/short-puts")).toContain("Our rule has not scored this list yet");
    payload = { ...payload, scored: true };
  });
});
