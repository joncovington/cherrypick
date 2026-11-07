import { afterEach, describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { EarningsCalendarPayload, EarningsCalendarRow, EarningsTrade } from "@console/shared";
import { TradeCard } from "../src/pages/Live/TradeCard";
import { writePref } from "../src/lib/prefs";

/**
 * The earnings tab (redesign 2026-11-06): what its defaults keep (the vendor's view: strong reads,
 * very liquid, optionable, US, a week), how rows group by day, that it opens in ROWS with what used to
 * be hover-only inside each row's card, and that the no-edge note is an amber flag with its text behind
 * the ?. It decides nothing, so the tests are about the cuts and the cells. Dates are pinned to an older
 * calendar, whose windows count from its own session rather than today.
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
    eventExpiryDays: 2,
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

const { EarningsCalendarTab, earningsModel, CALENDAR_SORTS, calendarHelp } = await import("../src/pages/Screeners/EarningsCalendarTab");

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

/** The symbols drawn, in order: a row's, or a card's. */
const symbols = (html: string) => [...html.matchAll(/class="(?:tr-sym|pos-card-sym)">([A-Z]+)</g)].map((m) => m[1]);
const card = (r: EarningsCalendarRow) =>
  renderToString(<TradeCard m={earningsModel(r)} />)
    .replace(/<!-- -->/g, "")
    .replace(/&#x27;/g, "'");

const leg = (role: "long" | "short", strike: number, passes = true) => ({ role, strike, bid: 1, ask: 1.1, openInterest: 500, volume: 50, passes });
const trade = (over: Partial<EarningsTrade> = {}): EarningsTrade => ({
  readingAt: "2026-10-19T15:30:00Z",
  takenOn: "2026-10-19",
  afterReport: false,
  kind: "credit",
  right: "P",
  expiry: "2026-11-20",
  width: 5,
  legs: [leg("long", 95), leg("short", 100)],
  mid: 1.75,
  natural: 1.6,
  shareOfWidth: 0.35,
  maxLoss: 325,
  passes: true,
  ...over,
});

afterEach(() => {
  void writePref("view.earnings-calendar", undefined);
  void writePref("sort.earnings-calendar", undefined);
});

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

  it("opens in rows, one a name and closed, and the switch to cards draws each in full", () => {
    const rows = render("/screeners/earnings?day=2026-10-15");
    expect(rows.match(/class="tr-row"/g)?.length).toBe(4);
    expect(rows).not.toContain("tc-card");
    expect(rows).toContain('aria-label="Rows"');
    void writePref("view.earnings-calendar", "cards");
    const cards = render("/screeners/earnings?day=2026-10-15");
    expect(cards.match(/tc-card/g)?.length).toBeGreaterThanOrEqual(4);
    expect(cards).not.toContain('class="tr-row"');
  });

  it("groups by day under a heading with its count", () => {
    const html = render("/screeners/earnings?day=2026-10-15");
    expect(html).toContain("Tuesday, Oct 20 · 2");
    expect(html).toContain("Wednesday, Oct 21 · 2");
  });

  it("puts what was hover-only inside the row's card: company, sector, IV rank, close, the move and its expiry", () => {
    const html = card(row({ trade: trade() }));
    expect(html).toContain("Alpha Inc.");
    expect(html).toContain("strength 9/10 · Financials");
    expect(html).toContain("IV rank 67% · close $100.00");
    expect(html).toContain("move ±4.8% (±$4.75) to the 23 Oct expiry +2d");
    expect(html).toContain("enter by the Oct 20 close · reacts Oct 21");
    // The read is ours and says so where it is read.
    expect(html).toContain("Ours (Bearing), from our bars as of the Oct 15 close.");
  });

  it("shows each leg as the order chain with its bid and ask, open interest and volume, and whether the spread passes", () => {
    const html = card(row({ trade: trade() }));
    // A credit spread reads short first: STO the 100, BTO the 95.
    expect(html.indexOf("100P")).toBeLessThan(html.indexOf("95P"));
    expect(html).toContain(">STO<");
    expect(html).toContain(">BTO<");
    expect(html).toContain("1.00 / 1.10 · OI 500 · vol 50");
    expect(html).toContain("1.75 cr");
    expect(html).toContain("+$175.00");
    expect(html).toContain("liquid: every leg passes");
    expect(html).toContain("35% of width · max loss $325.00");
    expect(html).toContain("the spread has no edge in history");
  });

  it("a debit is signed as cash paid", () => {
    const html = card(row({ suggestion: { kind: "debit", right: "C", long: 100, short: 105, expiry: "2026-11-20" }, trade: trade({ kind: "debit", right: "C", legs: [leg("long", 100), leg("short", 105)], mid: 2 }) }));
    expect(html).toContain("-$200.00");
    expect(html).toContain("2.00 db");
  });

  it("a spread read as thin is hidden by default, one read as liquid shows its price", () => {
    const saved = payload.rows;
    payload.rows = [row({ symbol: "GOOD", trade: trade() }), row({ symbol: "THIN", trade: trade({ passes: false, legs: [leg("long", 95, false), leg("short", 100)] }) }), row({ symbol: "UNREAD" })];
    try {
      expect(symbols(render("/screeners/earnings?day=2026-10-15"))).toEqual(["GOOD", "UNREAD"]);
      expect(symbols(render("/screeners/earnings?day=2026-10-15&trade=all"))).toEqual(["GOOD", "THIN", "UNREAD"]);
      expect(card(payload.rows[1]!)).toContain("thin: the spread fails the liquidity bars");
      expect(card(payload.rows[1]!)).toContain("fails");
    } finally {
      payload.rows = saved;
    }
  });

  it("a target price never reads as a strike: targets are marked, listed strikes say they are not priced", () => {
    const target = card(row({ symbol: "TGT" }));
    expect(target).toContain("≈ 100.00P (target)");
    expect(target).toContain("target prices, not strikes");
    const listed = card(row({ symbol: "LST", listed: { expiry: "2026-11-20", strikes: [95, 100] } }));
    expect(listed).toContain("1 2026-11-20 100P");
    expect(listed).toContain("listed, not priced yet");
    expect(listed).not.toContain("target");
  });

  it("says when we hold no bars, and shows a no-spread name without a leg", () => {
    const html = card(row({ symbol: "NONE", edge: null, edgeLabel: null, strength: null, readSession: null, suggestion: null }));
    expect(html).toContain("We hold no bars for this name, so no read.");
    expect(html).toContain("no spread suggested");
    expect(html).not.toContain(">STO<");
  });

  it("an unknown time and an estimated time and date say so", () => {
    const html = render("/screeners/earnings?day=2026-10-15");
    expect(html).toContain("time unknown");
    expect(html).toContain("BMO est.");
    expect(html).toContain(">est.<");
    expect(html).toContain("No source gives the time of day");
  });

  it("flags an expected move priced weeks past the reaction", () => {
    expect(card(row({ symbol: "WEEK" }))).not.toContain("a longer move");
    expect(card(row({ symbol: "FIX", eventExpiry: "2026-11-20", eventExpiryDays: 30 }))).toContain("a longer move than the announcement's");
  });

  it("warns when the close is older than the build", () => {
    expect(render("/screeners/earnings?day=2026-10-15")).not.toContain("prices from 2026-10-14");
    const saved = payload.inputs;
    payload.inputs = { ...saved, closeDate: "2026-10-14" };
    try {
      const html = render("/screeners/earnings?day=2026-10-15");
      expect(html).toContain("prices from the 2026-10-14 close");
      expect(html).toContain("prices from 2026-10-14, not 2026-10-15");
    } finally {
      payload.inputs = saved;
    }
  });

  it("marks a spread read after the announcement", () => {
    expect(card(row({ trade: trade({ afterReport: true }) }))).toContain("read after the report: not an entry price");
    expect(card(row({ trade: trade({ afterReport: false }) }))).not.toContain("read after the report");
  });

  it("hides a spread priced under the premium floor, keeps an unpriced one, and says so in the summary", () => {
    const saved = payload.rows;
    payload.rows = [row({ symbol: "BIGP", trade: trade({ mid: 1.2 }) }), row({ symbol: "SMLP", trade: trade({ mid: 0.15 }) }), row({ symbol: "NOPX", trade: trade({ mid: null }) })];
    try {
      const off = render("/screeners/earnings?day=2026-10-15");
      expect(symbols(off)).toEqual(["BIGP", "NOPX", "SMLP"]);
      const html = render("/screeners/earnings?day=2026-10-15&premium=25");
      expect(symbols(html)).toEqual(["BIGP", "NOPX"]);
      expect(html).toContain("≥ $25");
    } finally {
      payload.rows = saved;
    }
  });

  it("the summary line says what is on, and the button counts what is not at its default", () => {
    const html = render("/screeners/earnings?day=2026-10-15");
    for (const part of [">next 7 days<", ">strong reads<", ">very liquid<", ">liquid trade<"]) expect(html).toContain(part);
    expect(html).toContain("4 of 8 announcements");
    expect(html).toContain('aria-label="Filter and sort"');
    expect(render("/screeners/earnings?day=2026-10-15&dir=all&time=amc")).toContain('aria-label="Filter and sort, 2 changed"');
  });

  it("sorts in the day's group by timing, size and fit, never by credit", () => {
    expect(CALENDAR_SORTS.map((s) => s.id)).toEqual(["symbol", "time", "strength", "move"]);
    const saved = payload.rows;
    payload.rows = [row({ symbol: "AAA", strength: 5 }), row({ symbol: "ZZZ", strength: 10 })];
    try {
      expect(symbols(render("/screeners/earnings?day=2026-10-15"))).toEqual(["AAA", "ZZZ"]);
      void writePref("sort.earnings-calendar", { id: "strength", reversed: false });
      expect(symbols(render("/screeners/earnings?day=2026-10-15"))).toEqual(["ZZZ", "AAA"]);
      void writePref("sort.earnings-calendar", { id: "strength", reversed: true });
      expect(symbols(render("/screeners/earnings?day=2026-10-15"))).toEqual(["AAA", "ZZZ"]);
    } finally {
      payload.rows = saved;
    }
  });

  it("says the suggested spread has no edge as an amber flag, its text behind the ?", () => {
    const html = render("/screeners/earnings?day=2026-10-15");
    expect(html).toMatch(/class="help-flag"[^>]*>no edge in history</);
    expect(html).toContain('aria-label="About earnings calendar"');
    // The long note is no longer in the card body.
    expect(html).not.toContain("The suggested spread has no edge in history.");
    const help = renderToString(<>{calendarHelp(50).body}</>).replace(/<!-- -->/g, "");
    expect(help).toContain("The suggested spread has no edge in history.");
    expect(help).toContain("lost 4.3% on");
  });
});
