import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { SkateBandRead, SkateLeg, SkatePayload, SkateRow } from "@console/shared";
import { skateCounts, skateViews } from "../src/pages/Screeners/skateRows";

/**
 * The Skate tab: hard filters hide (a row is the band's pick on its best expiry, only when it passed),
 * soft thresholds mark and only narrow under "passing only", the default order is history minus
 * implied, the band selector picks which band drives the rows, the other band sits beside them, and
 * the run's state (none, unreadable, incomplete, a problem) is said rather than shown as no trades.
 */

function leg(over: Partial<SkateLeg>): SkateLeg {
  return {
    expiry: "2026-11-20",
    days: 32,
    role: "pick",
    option: "X",
    sameBand: false,
    strike: 110,
    offset: 0.004,
    outsideGrid: false,
    quote: { bid: 1.25, ask: 1.3, volume: 40, openInterest: 900 },
    mid: 1.275,
    iv: 0.3,
    delta: 0.16,
    impliedSkate: 0.86,
    skate: 0.9,
    skateRecent: 0.85,
    gap: 0.04,
    yieldBid: 0.12,
    yieldMid: 0.13,
    perDayBid: 3.91,
    testedPerCycle: 0.1,
    testedPerMonth: 0.11,
    feeOpen: 1.14,
    netPremiumBid: 123.86,
    netYieldBid: 0.118,
    historyWindows: 2772,
    historyWindowsRecent: 750,
    hard: true,
    soft: { skate: true, skateRecent: true, yield: true, all: true },
    ...over,
  };
}

function band(pick: Partial<SkateLeg> | null, vsMonthly: number | null = 1): SkateBandRead {
  if (pick === null) return { best: null, pick: null };
  const l = leg(pick);
  return { best: { expiry: l.expiry, strike: l.strike, perDayBid: l.perDayBid, skate: l.skate, monthly: "2026-11-20", vsMonthly }, pick: l };
}

function row(symbol: string, fixed: SkateBandRead, byExpiry: SkateBandRead, side: "C" | "P" = "C"): SkateRow {
  return { symbol, side, spot: 100, fund: false, earnings: "2027-01-28", divYield: 0, stage: 2, monthly: "2026-11-20", bands: { fixed, byExpiry } };
}

const rows: SkateRow[] = [
  row("LOW", band({ gap: 0.01 }), band({ gap: 0.2, strike: 104 })),
  row("HIGH", band({ gap: 0.08, expiry: "2026-10-30", days: 11, strike: 107 }, 1.6), band({ gap: 0.02, strike: 105, expiry: "2026-10-30", perDayBid: 5.5 })),
  row("SOFTMISS", band({ gap: 0.05, soft: { skate: false, skateRecent: true, yield: true, all: false } }), band(null)),
  row("NOGAP", band({ gap: null }), band(null)),
  // The band's pick failed the hard filters: hidden, whatever its gap.
  row("THIN", band({ gap: 0.5, hard: false }), band(null)),
  // No best on the band at all.
  row("NONE", band(null), band({ gap: 0.1 })),
  row("PUTA", band({ gap: 0.03, strike: 90 }), band(null), "P"),
];

const order = (vs: ReturnType<typeof skateViews>) => vs.map((v) => v.row.symbol);

describe("skateViews", () => {
  it("shows only the band's hard-passing pick, widest gap first, a missing gap last", () => {
    expect(order(skateViews(rows, "C", "fixed", false))).toEqual(["HIGH", "SOFTMISS", "LOW", "NOGAP"]);
    expect(order(skateViews(rows, "P", "fixed", false))).toEqual(["PUTA"]);
  });

  it("narrows to the soft thresholds only when asked", () => {
    expect(order(skateViews(rows, "C", "fixed", true))).toEqual(["HIGH", "LOW", "NOGAP"]);
  });

  it("lets the band selector drive the rows and the ranking, with the other band beside", () => {
    const by = skateViews(rows, "C", "byExpiry", false);
    expect(order(by)).toEqual(["LOW", "NONE", "HIGH"]);
    expect(by[0]!.pick.strike).toBe(104);
    expect(by[0]!.other.best?.strike).toBe(110);
  });

  it("counts what it hides", () => {
    expect(skateCounts(rows, "C", "fixed")).toEqual({ read: 6, tradable: 4, passing: 3 });
  });
});

let payload: SkatePayload;
const base: SkatePayload = {
  status: "ok",
  day: "2026-10-19",
  days: ["2026-10-19"],
  run: "1505",
  runs: ["1505", "1200"],
  measuredAt: "2026-10-19T19:05:01+00:00",
  bandDay: "2026-10-16",
  complete: true,
  problem: null,
  config: {
    haircut: 0.75,
    byExpiry: [[14, 0.65]],
    hard: { minOpenInterest: 100, maxSpreadPct: 0.1, maxSpreadAbs: 0.05 },
    soft: { skate: 0.84, skateRecent: 0.8, yield: 0.06 },
    stageTwo: 60,
    monthDays: 35,
  },
  rows,
};

vi.mock("../src/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/lib/api")>()),
  useSkate: () => ({ data: payload, isLoading: false, isError: false }),
  useSkateRecord: () => ({
    data: {
      status: "none",
      writtenAt: null,
      recorded: null,
      added: {},
      settledNow: null,
      settled: null,
      expiries: null,
      decisionGrade: false,
      bands: { fixed: null, byExpiry: null },
      test: null,
    },
    isLoading: false,
    isError: false,
  }),
}));

const { SkateTab } = await import("../src/pages/Screeners/SkateTab");

function render(url: string, p: SkatePayload = base): string {
  payload = p;
  return renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[url]}>
        <SkateTab />
      </MemoryRouter>
    </QueryClientProvider>,
  )
    .replace(/<!-- -->/g, "")
    .replace(/&#x27;/g, "'");
}

const symbols = (html: string) => [...html.matchAll(/symbol=([A-Z]+)"/g)].map((m) => m[1]);

describe("the skate tab", () => {
  it("lists calls on the fixed band by default, and puts and the other band on request", () => {
    expect(symbols(render("/screeners/skate"))).toEqual(["HIGH", "SOFTMISS", "LOW", "NOGAP"]);
    expect(symbols(render("/screeners/skate?side=puts"))).toEqual(["PUTA"]);
    expect(symbols(render("/screeners/skate?band=by-expiry"))).toEqual(["LOW", "NONE", "HIGH"]);
    expect(symbols(render("/screeners/skate?passing=1"))).toEqual(["HIGH", "LOW", "NOGAP"]);
  });

  it("marks the soft thresholds, bolds a best expiry that beats the monthly, and states money per contract", () => {
    const html = render("/screeners/skate");
    expect(html).toContain('<span class="chip chip-ok">pass</span>');
    expect(html).toContain('<span class="chip">miss</span>');
    expect(html).toContain("Misses: full-history skate rate under 84%");
    expect(html).toMatch(/font-weight:700[^>]*>Oct 30/);
    expect(html).not.toMatch(/font-weight:700[^>]*>Nov 20/);
    expect(html).toContain("1.25 bid");
    expect(html).toContain("+$125");
    // HIGH's 107 strike on a 100 spot.
    expect(html).toContain(">+7.0%<");
    expect(html).toContain("+8.0 pts");
    // The other band beside: HIGH's by-expiry best is 105 on the same expiry.
    expect(html).toContain("105 · $5.50/d");
    expect(html).toContain("measured 2026-10-19 15:05 ET");
    expect(html).toContain("bands on the 2026-10-16 close");
    expect(html).toContain("a history estimate");
    // The forward record's section sits below the table.
    expect(html).toContain("Forward record");
    expect(html).toContain("No forward record written yet");
  });

  it("shows the fee beside the gross premium, and marks a premium that does not cover it", () => {
    const html = render("/screeners/skate");
    expect(html).toContain("Premium, gross");
    expect(html).toContain('+$123.86<span class="muted"> fee $1.14</span>');
    expect(html).toContain("11.8%"); // the net yield
    // KHC-style: a one-cent bid, a dollar of premium, under the fee to open.
    const tiny = row("TINY", band({ quote: { bid: 0.01, ask: 0.02, volume: 1, openInterest: 900 }, netPremiumBid: -0.14, netYieldBid: -0.001 }), band(null));
    const under = render("/screeners/skate?q=TINY", { ...base, rows: [tiny] });
    expect(under).toContain("−$0.14");
    expect(under).toContain(">under cost<");
    // A run older than the fee fields shows a dash, never a zero fee.
    const old = row("OLD", band({ feeOpen: null, netPremiumBid: null, netYieldBid: null }), band(null));
    expect(render("/screeners/skate?q=OLD", { ...base, rows: [old] })).toContain("This run predates the fee figures");
  });

  it("states how much history the skate rate stands on, and keeps 'through' apart from the record's 'tested'", () => {
    const html = render("/screeners/skate");
    expect(html).toContain("Skate, full history");
    expect(html).not.toContain("Skate 11y");
    expect(html).toContain('<span class="muted"> 11.0y</span>');
    const young = row("SNOW", band({ historyWindows: 1514 }), band(null));
    expect(render("/screeners/skate?q=SNOW", { ...base, rows: [young] })).toContain('<span class="muted"> 6.0y</span>');
    expect(html).toContain("Through / mo");
    expect(html).not.toContain("Tested / mo");
  });

  it("says when there is no run, an unreadable run, an incomplete run or a stated problem", () => {
    expect(render("/screeners/skate", { ...base, status: "none", day: null, days: [], run: null, runs: [], rows: [] })).toContain(
      "No skate run written yet",
    );
    expect(render("/screeners/skate", { ...base, status: "unreadable", rows: [] })).toContain("could not be read");
    const partial = render("/screeners/skate", { ...base, complete: false, problem: "chain fetch failed" });
    expect(partial).toContain("did not complete");
    expect(partial).toContain("The run reports a problem: chain fetch failed");
    expect(render("/screeners/skate?side=puts&passing=1", { ...base, rows: [rows[4]!] })).toContain("No puts on the fixed band pass the soft thresholds");
  });
});
