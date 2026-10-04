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
    trendAgrees: true,
    entryDate: "2026-10-01",
    entryPrice: 32.12,
    entryAgo: 1,
    exitDate: null,
    exitPrice: null,
    exitAgo: null,
    reason: null,
    target: null,
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

let payload: TechnicalsWatchlist = { session: "2026-10-02", generatedAt: null, window: 20, rows: ROWS };

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
    payload = { session: "2026-10-02", generatedAt: null, window: 20, rows: ROWS };
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
    payload = { session: "2026-10-02", generatedAt: null, window: 20, rows: ROWS };
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

  it("an empty file says the report job writes it", () => {
    payload = { session: null, generatedAt: null, window: null, rows: [] };
    expect(render("/charts/setups")).toContain("No watchlist yet");
  });
});
