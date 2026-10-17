import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ScreenerFidelityPayload, ScreenerPayload, ScreenerRow } from "@console/shared";

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
    listedLegs: [{ role: "short", right: "P", expiry: "2026-11-13", strike: 98, bid: 1.9, ask: 2.1, openInterest: 500, volume: 50, passes: true }],
    cycleLegs: [{ role: "short", right: "P", expiry: "2026-11-20", strike: 98, bid: 2.2, ask: 2.3, openInterest: 900, volume: 40, passes: true }],
    recommendation: "listed",
    cyclePasses: true,
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

const R = (hit: number, n: number) => ({ hit, n });
const SESSION = {
  day: "2026-10-13",
  shortPuts: { monthly: R(1, 1), weekly: R(330, 450), expiry: R(450, 450), returns: R(450, 450) },
  coveredCalls: { monthly: R(0, 1), weekly: R(230, 460), expiry: R(0, 0), returns: R(400, 460) },
  creditSpreads: { expiry: R(30, 30), short: R(30, 30), long: R(19, 30), floor: R(30, 30) },
};
const fidelity: ScreenerFidelityPayload = { sessions: [SESSION], totals: SESSION };

vi.mock("../src/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/lib/api")>()),
  useScreener: () => ({ data: payload, isLoading: false, isError: false }),
  useScreenerFidelity: () => ({ data: fidelity, isLoading: false, isError: false }),
}));

const { FidelityTab, ScreenerTab } = await import("../src/pages/Screeners/ScreenersPage");

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

  it("an unscored list says when its picks arrive", () => {
    payload = { ...payload, scored: false };
    expect(render("/screeners/short-puts")).toContain("Our rule has not scored this list yet");
    payload = { ...payload, scored: true };
  });
});

describe("the fidelity tab", () => {
  it("shows a percentage only where there are enough rows to mean one", () => {
    const html = renderToString(<FidelityTab />).replace(/<!-- -->/g, "");
    expect(html).toContain('title="0 of 1">0/1<');
    expect(html).toContain('title="330 of 450">73%<');
    expect(html).toContain('title="0 of 0">—<');
  });
});
