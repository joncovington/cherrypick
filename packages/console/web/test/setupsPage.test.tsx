import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { TechnicalsWatchlist, TechnicalsWatchlistRow } from "@console/shared";

/**
 * The setups watchlist page: which rows each view and filter shows, and that a symbol opens its
 * chart with the row's setup selected. Hooks are mocked so the loaded state is what renders.
 */

function row(over: Partial<TechnicalsWatchlistRow>): TechnicalsWatchlistRow {
  return {
    symbol: "HPQ",
    session: "2026-10-02",
    setup: "trend",
    setupName: "Trend following",
    family: "trend",
    side: "long",
    tested: null,
    trendAgrees: true,
    entryDate: "2026-10-01",
    entryPrice: 32.12,
    entryAgo: 1,
    exitDate: null,
    exitPrice: null,
    exitAgo: null,
    reason: null,
    target: null,
    dollarVolume: 4.2e8,
    status: "open",
    lastClose: 32.09,
    movePct: -0.09,
    trend1m: 2,
    trend6m: 3,
    trend1mLabel: "Bullish",
    trend6mLabel: "Bullish",
    rs: 10,
    vsSpy1m: 0.54,
    support: { value: 30.71, pct: -4.39 },
    resistance: null,
    optionsTradable: null,
    read: null,
    ...over,
  };
}

const ROWS = [
  row({}),
  // Entered 12 sessions ago, exited 2 ago: an exit in the 5-day window, its entry outside it.
  row({ symbol: "ABBV", setup: "pullback", setupName: "Pullback", family: "pullback", entryDate: "2026-09-16", entryAgo: 12, exitDate: "2026-09-30", exitPrice: 240, exitAgo: 2, reason: "target", status: "closed", rs: 5, trend6mLabel: "Neutral", trendAgrees: false }),
  // Open for 30 sessions: only in the open view.
  row({ symbol: "ABT", setup: "reversion", setupName: "Mean reversion", family: "reversion", entryDate: "2026-08-19", entryAgo: 30, rs: 3, trendAgrees: false }),
  // A short, entered today and still open; its trends agree (both below zero).
  row({ symbol: "NKE", setup: "trend-short", setupName: "Trend following (short)", family: "trend", side: "short", entryDate: "2026-10-02", entryAgo: 0, rs: 2, trend1m: -3, trend6m: -4, trend1mLabel: "Bearish", trend6mLabel: "Bearish" }),
];

let payload: TechnicalsWatchlist = { session: "2026-10-02", generatedAt: null, window: 20, optionsLabelDay: null, rows: ROWS };

vi.mock("../src/lib/api", () => ({
  useSetupsWatchlist: () => ({ data: payload, isLoading: false, isError: false }),
}));

const { SetupsPage } = await import("../src/pages/Setups/SetupsPage");

function render(url: string): string {
  const html = renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[url]}>
        <SetupsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return html.replace(/<!-- -->/g, "").replace(/&#x27;/g, "'").replace(/&amp;/g, "&");
}

const symbols = (html: string) =>
  [...html.matchAll(/href="\/charts\/technicals\?symbol=([A-Z]+)&setup=(\w+)(&side=short)?"/g)].map((m) => `${m[1]}:${m[2]}${m[3] ? ":short" : ""}`);

describe("the setups watchlist page", () => {
  it("lists the last 5 sessions' entries and exits, each symbol linking to its chart with that setup", () => {
    payload = { session: "2026-10-02", generatedAt: null, window: 20, optionsLabelDay: null, rows: ROWS };
    const html = render("/charts/setups");
    expect(symbols(html)).toEqual(["NKE:trend:short", "HPQ:trend", "ABBV:pullback"]);
    expect(html).toContain("▲ entry");
    expect(html).toContain("▼ exit · target");
    expect(html).toContain("▼ short");
  });

  it("the open view lists every open position, however old", () => {
    expect(symbols(render("/charts/setups?view=open"))).toEqual(["NKE:trend:short", "HPQ:trend", "ABT:reversion"]);
  });

  it("filters by setup, trend agreement and strength, as the URL says", () => {
    expect(symbols(render("/charts/setups?setup=pullback"))).toEqual(["ABBV:pullback"]);
    // The family filter takes in both sides; the side filter narrows it.
    expect(symbols(render("/charts/setups?setup=trend"))).toEqual(["NKE:trend:short", "HPQ:trend"]);
    expect(symbols(render("/charts/setups?side=short"))).toEqual(["NKE:trend:short"]);
    expect(symbols(render("/charts/setups?side=long"))).toEqual(["HPQ:trend", "ABBV:pullback"]);
    // Trend agrees is the package's flag: a short agrees when its trends are below zero.
    expect(symbols(render("/charts/setups?agree=1"))).toEqual(["NKE:trend:short", "HPQ:trend"]);
    expect(symbols(render("/charts/setups?view=open&rs7=1"))).toEqual(["HPQ:trend"]);
    expect(symbols(render("/charts/setups?event=exit"))).toEqual(["ABBV:pullback"]);
    // 20 sessions takes in ABBV's entry as well as its exit; sorted by RS, HPQ (10) leads.
    expect(symbols(render("/charts/setups?window=20&sort=rs&side=long"))).toEqual(["HPQ:trend", "ABBV:pullback", "ABBV:pullback"]);
  });

  it("every sortable column reverses on a second click, and a blank stays at the bottom either way", () => {
    payload = { session: "2026-10-02", generatedAt: null, window: 20, optionsLabelDay: null, rows: ROWS };
    // Ago: newest first by default, oldest first reversed.
    expect(symbols(render("/charts/setups?side=long"))).toEqual(["HPQ:trend", "ABBV:pullback"]);
    expect(symbols(render("/charts/setups?side=long&dir=desc"))).toEqual(["ABBV:pullback", "HPQ:trend"]);
    // RS: strongest first by default, weakest first reversed.
    expect(symbols(render("/charts/setups?view=open&side=long&sort=rs"))).toEqual(["HPQ:trend", "ABT:reversion"]);
    expect(symbols(render("/charts/setups?view=open&side=long&sort=rs&dir=asc"))).toEqual(["ABT:reversion", "HPQ:trend"]);
    const unranked = row({ symbol: "ZZZ", rs: null });
    payload = { ...payload, rows: [...ROWS, unranked] };
    for (const dir of ["", "&dir=asc"]) {
      expect(symbols(render(`/charts/setups?view=open&side=long&sort=rs${dir}`)).at(-1)).toBe("ZZZ:trend");
    }
    payload = { ...payload, rows: ROWS };
  });

  // The tested rule's rows sit beside the setup rows they overlap: ABT's reversion is in both.
  const TESTED = [
    ...ROWS,
    row({ symbol: "ABT", setup: "reversion", setupName: "Mean reversion", family: "reversion", tested: "mr-300m", entryDate: "2026-10-01", entryAgo: 1, trendAgrees: false, dollarVolume: 8.5e8 }),
    row({ symbol: "HD", setup: "reversion", setupName: "Mean reversion", family: "reversion", tested: "mr-300m", entryDate: "2026-10-02", entryAgo: 0, trendAgrees: false, dollarVolume: 1.3e9 }),
  ];

  it("the tested view lists only the confirmed rule's rows, whatever the setup, side and agree filters say", () => {
    payload = { session: "2026-10-02", generatedAt: null, window: 20, optionsLabelDay: null, rows: TESTED };
    expect(symbols(render("/charts/setups?tested=1"))).toEqual(["HD:reversion", "ABT:reversion"]);
    // Trend agrees would hide both (an oversold entry never has a positive 1-month trend): ignored.
    expect(symbols(render("/charts/setups?tested=1&agree=1&setup=trend&side=short"))).toEqual(["HD:reversion", "ABT:reversion"]);
    // And the default view never shows them, so no trade is listed twice.
    expect(symbols(render("/charts/setups?window=20"))).not.toContain("HD:reversion");
    const html = render("/charts/setups?tested=1");
    expect(html).toContain("at least $300M a day");
    expect(html).toContain("$1.3B");
    expect(html).toContain("$850M");
  });

  it("says when Trend agrees hides every long mean-reversion signal", () => {
    payload = { session: "2026-10-02", generatedAt: null, window: 20, optionsLabelDay: null, rows: ROWS };
    const note = "Trend agrees hides every long mean-reversion signal";
    expect(render("/charts/setups?agree=1")).toContain(note);
    expect(render("/charts/setups?agree=1&setup=reversion")).toContain(note);
    expect(render("/charts/setups?agree=1&setup=trend")).not.toContain(note);
    expect(render("/charts/setups?agree=1&side=short")).not.toContain(note);
    expect(render("/charts/setups")).not.toContain(note);
  });

  it("cuts to options-tradable names by default when the file carries the label, with a way out", () => {
    const labelled = ROWS.map((r) => ({ ...r, optionsTradable: r.symbol !== "ABBV" }));
    payload = { session: "2026-10-02", generatedAt: null, window: 20, optionsLabelDay: "2026-10-02", rows: labelled };
    expect(symbols(render("/charts/setups"))).toEqual(["NKE:trend:short", "HPQ:trend"]);
    const all = render("/charts/setups?names=all");
    expect(symbols(all)).toEqual(["NKE:trend:short", "HPQ:trend", "ABBV:pullback"]);
    expect(all).toContain("· no opts");
    // An unknown flag is not a yes: a row the label could not judge is cut too.
    payload = { ...payload, rows: [...labelled, row({ symbol: "ZZZ", optionsTradable: null })] };
    expect(symbols(render("/charts/setups"))).not.toContain("ZZZ:trend");
  });

  it("with no label at all, nothing is cut and the page says so", () => {
    payload = { session: "2026-10-02", generatedAt: null, window: 20, optionsLabelDay: null, rows: ROWS };
    const html = render("/charts/setups");
    expect(symbols(html)).toEqual(["NKE:trend:short", "HPQ:trend", "ABBV:pullback"]);
    expect(html).toContain("no options label");
  });

  it("sorts by dollar volume, largest first", () => {
    const rows = [row({ symbol: "AAA", dollarVolume: 1e8 }), row({ symbol: "BBB", dollarVolume: 2e9 }), row({ symbol: "CCC", dollarVolume: null })];
    payload = { session: "2026-10-02", generatedAt: null, window: 20, optionsLabelDay: null, rows };
    expect(symbols(render("/charts/setups?view=open&sort=dvol"))).toEqual(["BBB:trend", "AAA:trend", "CCC:trend"]);
    expect(symbols(render("/charts/setups?view=open&sort=dvol&dir=asc"))).toEqual(["AAA:trend", "BBB:trend", "CCC:trend"]);
  });

  it("sorts by the line's price and by the last close, highest first", () => {
    const rows = [
      row({ symbol: "AAA", entryPrice: 10, lastClose: 50 }),
      row({ symbol: "BBB", entryPrice: 30, lastClose: 20 }),
      row({ symbol: "CCC", entryPrice: 20, lastClose: null }),
    ];
    payload = { session: "2026-10-02", generatedAt: null, window: 20, optionsLabelDay: null, rows };
    expect(symbols(render("/charts/setups?view=open&sort=price"))).toEqual(["BBB:trend", "CCC:trend", "AAA:trend"]);
    expect(symbols(render("/charts/setups?view=open&sort=price&dir=asc"))).toEqual(["AAA:trend", "CCC:trend", "BBB:trend"]);
    // A missing last close stays at the bottom either way.
    expect(symbols(render("/charts/setups?view=open&sort=last"))).toEqual(["AAA:trend", "BBB:trend", "CCC:trend"]);
    expect(symbols(render("/charts/setups?view=open&sort=last&dir=asc"))).toEqual(["BBB:trend", "AAA:trend", "CCC:trend"]);
    // An exit line sorts by its exit close, not the entry's.
    payload = { ...payload, rows: [row({ symbol: "AAA", entryPrice: 10, exitDate: "2026-10-02", exitPrice: 99, exitAgo: 0, entryAgo: 30, status: "closed" }), row({ symbol: "BBB", entryPrice: 50 })] };
    expect(symbols(render("/charts/setups?sort=price"))).toEqual(["AAA:trend", "BBB:trend"]);
    // Entry and Last left the row for its expanded line on 2026-10-30; the URL still sorts by them.
    const html = render("/charts/setups?view=open");
    expect(html).not.toMatch(/<button[^>]*>Entry<\/button>/);
    expect(html).not.toMatch(/<button[^>]*>Last<\/button>/);
  });

  it("sorts by symbol, by trend score, and by a level's distance from the close, nearest first", () => {
    const rows = [
      row({ symbol: "BBB", trend1m: 1, trend6m: 4, support: { value: 9, pct: -10 }, resistance: { value: 11, pct: 1 } }),
      row({ symbol: "AAA", trend1m: 3, trend6m: -2, support: { value: 9.8, pct: -2 }, resistance: null }),
      row({ symbol: "CCC", trend1m: null, trend6m: 0, support: null, resistance: { value: 15, pct: 5 } }),
    ];
    payload = { session: "2026-10-02", generatedAt: null, window: 20, optionsLabelDay: null, rows };
    expect(symbols(render("/charts/setups?view=open&sort=symbol"))).toEqual(["AAA:trend", "BBB:trend", "CCC:trend"]);
    expect(symbols(render("/charts/setups?view=open&sort=symbol&dir=desc"))).toEqual(["CCC:trend", "BBB:trend", "AAA:trend"]);
    expect(symbols(render("/charts/setups?view=open&sort=t1m"))).toEqual(["AAA:trend", "BBB:trend", "CCC:trend"]);
    expect(symbols(render("/charts/setups?view=open&sort=t6m"))).toEqual(["BBB:trend", "CCC:trend", "AAA:trend"]);
    // Support is below the close (a negative pct): nearest first means smallest distance.
    expect(symbols(render("/charts/setups?view=open&sort=support"))).toEqual(["AAA:trend", "BBB:trend", "CCC:trend"]);
    expect(symbols(render("/charts/setups?view=open&sort=support&dir=desc"))).toEqual(["BBB:trend", "AAA:trend", "CCC:trend"]);
    expect(symbols(render("/charts/setups?view=open&sort=resistance"))).toEqual(["BBB:trend", "CCC:trend", "AAA:trend"]);
    // An unknown sort is the default, not a crash.
    expect(symbols(render("/charts/setups?view=open&sort=bogus"))).toHaveLength(3);
  });

  it("an empty file says the report job writes it", () => {
    payload = { session: null, generatedAt: null, window: null, optionsLabelDay: null, rows: [] };
    expect(render("/charts/setups")).toContain("No watchlist yet");
  });

  it("every row has a closed toggle to its read", () => {
    payload = { session: "2026-10-02", generatedAt: null, window: 20, optionsLabelDay: null, rows: ROWS };
    const html = render("/charts/setups?view=open");
    expect(html.match(/aria-expanded="false"/g)).toHaveLength(3);
    expect(html).not.toContain("md-cards");
  });
});

describe("the read behind a row", () => {
  const read: NonNullable<TechnicalsWatchlistRow["read"]> = {
    setup: { rule: "Enter on the dip.", change: null, medianHold: 8 },
    record: { entries: 56634, netR: -0.058, edgeR: -0.202, t: -8.8, verdict: "worse than random", hit: null, medianHold: null },
    direction: { verdict: "conflicts", counterTrend: false, trendAgrees: false, trendAgainst: true, board: null },
    volatility: {
      iv: 40, ivRank: 70, ivPct: 80, hv30: 25, ivHv: 1.6, regime: "rich", cycle: { expiry: "2026-11-20", days: 35 },
      expectedMove: { dollars: 12.4, pct: 12.4 }, earnings: "2026-11-05", earningsInCycle: true, exDividend: null,
    },
    levels: { atr14: 2, support: { value: 95, pct: -5, atr: -2.5, sigma: -0.4 }, resistance: null, target: null },
    lean: {
      structure: "call debit spread",
      why: "a report falls inside the cycle: no premium sold through it",
      legs: ["buy near the money, sell near +1σ, 112.40 (+12.4%)"],
      cautions: ["this setup tested worse than random (-0.202 R against random entry)", "earnings 2026-11-05 inside the cycle"],
    },
  };

  it("leads with the verdicts and lays out the six cards, the record and the cautions in words", async () => {
    const { ReadDetail } = await import("../src/pages/Setups/SetupsPage");
    const html = renderToString(<ReadDetail r={row({ read })} />).replace(/<!-- -->/g, "");
    for (const title of ["the setup", "its record", "direction", "volatility", "levels", "structure lean"]) {
      expect(html).toContain(`<h4>${title}</h4>`);
    }
    expect(html).toContain("call debit spread");
    expect(html).toContain("worse than random");
    expect(html).toContain("inside the cycle");
    expect(html).toContain("a lean, not a trade");
  });

  it("a row from an index without reads says so rather than drawing empty cards", async () => {
    const { ReadDetail } = await import("../src/pages/Setups/SetupsPage");
    expect(renderToString(<ReadDetail r={row({ read: null })} />)).toContain("No read for this row yet");
  });
});
