import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { SkateBandRead, SkateLeg, SkatePayload, SkateRow } from "@console/shared";
import { skateCounts, skateViews } from "../src/pages/Screeners/skateRows";
import { sortRows } from "../src/components/FilterSort";
import { writePref } from "../src/lib/prefs";

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

const { SkateTab, SkatePanel, SKATE_SORTS, skateHelp } = await import("../src/pages/Screeners/SkateTab");

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

const symbols = (html: string) => [...html.matchAll(/aria-label="([A-Z]+): (?:covered call|cash-secured put)/g)].map((m) => m[1]);

describe("the skate tab", () => {
  it("lists calls on the fixed band by default, and puts and the other band on request", () => {
    expect(symbols(render("/screeners/skate"))).toEqual(["HIGH", "SOFTMISS", "LOW", "NOGAP"]);
    expect(symbols(render("/screeners/skate?side=puts"))).toEqual(["PUTA"]);
    expect(symbols(render("/screeners/skate?band=by-expiry"))).toEqual(["LOW", "NONE", "HIGH"]);
    expect(symbols(render("/screeners/skate?passing=1"))).toEqual(["HIGH", "LOW", "NOGAP"]);
  });

  it("draws each name-side as a trade card, in cards by default, with the order that would open it", () => {
    const html = render("/screeners/skate");
    expect(html).toContain('class="pos-card tc-card"');
    expect(html).not.toContain("tr-list");
    expect(html).toContain("tc-grid");
    // HIGH: the 107 call on its Oct 30 expiry at the 1.25 bid, sold to open.
    expect(html).toContain("1 2026-10-30 107C @ 1.25");
    expect(html).toContain("is-sell");
    expect(html).not.toContain("<table");
  });

  it("marks the soft thresholds, and states money per contract with the fee beside it", () => {
    const html = render("/screeners/skate");
    expect(html).toContain('<span class="chip chip-ok" title="Passes every soft threshold">soft pass</span>');
    expect(html).toContain(">soft miss<");
    expect(html).toContain("Misses: full-history skate rate under 84%");
    expect(html).toContain("1.25 / 1.3");
    expect(html).toContain("+$123.86"); // the net of the fee, a credit +
    expect(html).toContain("net of the $1.14 fee");
    expect(html).toContain("net yield 11.8%");
    // HIGH's 107 strike on a 100 spot, and its gap to implied.
    expect(html).toContain("+7.0% to the strike");
    expect(html).toContain("+8.0 pts");
    // The other band beside: HIGH's by-expiry best is 105 on the same expiry.
    expect(html).toContain("by expiry band: 105 · $5.50/d");
    expect(html).toContain("1.60× the monthly, more a day");
    expect(html).toContain("measured 2026-10-19 15:05 ET");
    expect(html).toContain("bands on the 2026-10-16 close");
  });

  it("keeps the method behind the ? and says not a tested edge once, as an amber flag", () => {
    const html = render("/screeners/skate");
    expect(html).toContain('aria-label="About skate"');
    expect(html).toContain('<button type="button" class="help-flag" title="Open the ? for the full text">not a tested edge</button>');
    // Neither footer is on the page any more.
    expect(html).not.toContain("a history estimate");
    expect(html).not.toContain("The best expiry ranks premium a day");
    expect(html).not.toContain("Each row is one name-side");
    const help = renderToString(<>{skateHelp(base.config).body}</>).replace(/<!-- -->/g, "").replace(/&#x27;/g, "'");
    expect(help).toContain("The best expiry ranks premium a day; it is not a tested edge");
    expect(help).toContain("a history estimate");
    expect(help).toContain("open interest of 100 or more");
  });

  it("holds the premium slider inside the filter and sort panel, not on the card", () => {
    // The panel is drawn only while open, so the page itself carries no slider...
    expect(render("/screeners/skate")).not.toContain("premium-slider");
    // ...and the panel's own contents do.
    const panel = renderToString(
      <SkatePanel sort={{ id: "gap", reversed: false }} onSort={() => undefined} band="fixed" passingOnly={false} minPremium={25} hidden={6} q="" config={base.config} set={() => undefined} />,
    ).replace(/<!-- -->/g, "");
    expect(panel).toContain("premium-slider");
    expect(panel).toContain("Premium ≥ $25");
    expect(panel).toContain("6 hidden");
    expect(panel).toContain("Passing only");
    expect(panel).toContain("history − implied");
  });

  it("offers sorts on the gap, name, timing and delta, never on credit or yield", () => {
    expect(SKATE_SORTS.map((s) => s.id)).toEqual(["gap", "symbol", "expiry", "delta"]);
    const by = (id: string) => sortRows(skateViews(rows, "C", "fixed", false), SKATE_SORTS.find((s) => s.id === id), false).map((v) => v.row.symbol);
    expect(by("gap")).toEqual(["HIGH", "SOFTMISS", "LOW", "NOGAP"]);
    expect(by("symbol")).toEqual(["HIGH", "LOW", "NOGAP", "SOFTMISS"]);
    expect(by("expiry")[0]).toBe("HIGH"); // 11 days against 32
  });

  it("says what is on in a summary line, and counts what is not at its default on the button", () => {
    const html = render("/screeners/skate");
    expect(html).toContain('class="fs-summary"');
    expect(html).toContain(">calls<");
    expect(html).toContain(">fixed band<");
    expect(html).toContain("by history − implied");
    expect(html).not.toContain("fs-badge");
    const changed = render("/screeners/skate?band=by-expiry&passing=1&q=LOW");
    expect(changed).toContain('<span class="fs-badge">3</span>');
    expect(changed).toContain(">passing only<");
  });

  it("hides a pick under the premium floor at the bid, keeps one with no bid, and is off by default", () => {
    const rows = [
      row("BIGP", band({}), band(null)),
      row("SMLP", band({ quote: { bid: 0.2, ask: 0.25, volume: 40, openInterest: 900 } }), band(null)),
      row("NOBID", band({ quote: { bid: null, ask: 0.3, volume: 40, openInterest: 900 } }), band(null)),
    ];
    const off = render("/screeners/skate", { ...base, rows });
    expect(symbols(off)).toEqual(expect.arrayContaining(["BIGP", "SMLP", "NOBID"]));
    expect(off).toContain(">any premium<");
    const html = render("/screeners/skate?premium=25", { ...base, rows });
    expect(symbols(html)).toEqual(expect.arrayContaining(["BIGP", "NOBID"]));
    expect(symbols(html)).not.toContain("SMLP");
    expect(html).toContain("1 under $25 hidden");
    expect(html).toContain(">≥ $25<");
  });

  it("shows the net of the fee as the headline, and marks a premium that does not cover it", () => {
    const html = render("/screeners/skate");
    expect(html).toContain("+$123.86");
    expect(html).toContain("net of the $1.14 fee");
    // KHC-style: a one-cent bid, a dollar of premium, under the fee to open.
    const tiny = row("TINY", band({ quote: { bid: 0.01, ask: 0.02, volume: 1, openInterest: 900 }, netPremiumBid: -0.14, netYieldBid: -0.001 }), band(null));
    const under = render("/screeners/skate?q=TINY", { ...base, rows: [tiny] });
    expect(under).toContain("-$0.14");
    expect(under).toContain(">under cost<");
    // A run older than the fee fields shows the gross, said so, never a zero fee.
    const old = row("OLD", band({ feeOpen: null, netPremiumBid: null, netYieldBid: null }), band(null));
    const oldHtml = render("/screeners/skate?q=OLD", { ...base, rows: [old] });
    expect(oldHtml).toContain("gross: this run predates the fee figures");
    expect(oldHtml).toContain("+$125.00");
  });

  it("states how much history the skate rate stands on, and keeps 'through' apart from the record's 'tested'", () => {
    const html = render("/screeners/skate");
    expect(html).toContain("skated 90% of past days · 86% implied · +8.0 pts · 11.0y of history");
    const young = row("SNOW", band({ historyWindows: 1514 }), band(null));
    expect(render("/screeners/skate?q=SNOW", { ...base, rows: [young] })).toContain("6.0y of history");
    expect(html).toContain("through 11% a month");
    expect(html).not.toContain("tested a month");
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

  it("puts the forward record in its own card under its own question", () => {
    const html = render("/screeners/skate");
    expect(html).toContain("Is it holding up?");
    expect(html).toContain("<h2>forward record</h2>");
    expect(html).toContain("No forward record written yet");
  });
});

describe("cards or rows", () => {
  beforeEach(() => void writePref("view.skate", "rows"));
  afterEach(() => void writePref("view.skate", undefined));

  it("draws one row a name-side once rows are chosen, each opening its card in place", () => {
    const html = render("/screeners/skate");
    expect(html).toContain("tr-list");
    expect(html).not.toContain("tc-grid");
    expect(html.match(/class="tr-row"/g)).toHaveLength(4);
    expect(html).toContain('aria-expanded="false"');
  });
});
