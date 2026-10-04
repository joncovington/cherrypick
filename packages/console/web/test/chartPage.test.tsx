import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { TechnicalsChart, TechnicalsChartPayload } from "@console/shared";

/**
 * The technicals chart page around its chart: the setup picker, the chosen setup's rule, an open
 * position, and the statement that SPX's volume is SPY's. The chart itself is a canvas the server
 * render never draws; `pnpm ui-check` covers it. Hooks are mocked so the loaded state is what renders.
 */

const BARS = ["2026-09-30", "2026-10-01", "2026-10-02"].map((date, i) => ({
  date,
  open: 100 + i,
  high: 101 + i,
  low: 99 + i,
  close: 100.5 + i,
  volume: null,
}));

function chart(over: Partial<TechnicalsChart> = {}): TechnicalsChart {
  return {
    symbol: "SPX",
    session: "2026-10-02",
    generatedAt: null,
    bars: BARS,
    grid: null,
    cci14: [null, null, null],
    cci5: [null, null, null],
    rsi14: [null, null, null],
    trendShort: [null, null, null],
    trendLong: [null, null, null],
    rank: null,
    sentiment: null,
    ivRank: null,
    signals: [],
    setups: [
      { id: "trend", name: "Trend following", rule: "Exit on the first close under the 21 EMA.", lines: ["ema21"], trades: [] },
      {
        id: "breakout",
        name: "Breakout",
        rule: "Exit on the first close with Supertrend(10, 3) down.",
        lines: ["supertrend"],
        trades: [{ entryDate: "2026-10-01", entryPrice: 101.5, exitDate: null, exitPrice: null, reason: null, target: null }],
      },
    ],
    setupLines: { ema21: [1, 2, 3], supertrend: [1, 2, 3] },
    volumeSource: "SPY",
    vendor: null,
    ...over,
  };
}

let payload: TechnicalsChartPayload = { index: { session: null, symbols: [] }, chart: null };

vi.mock("../src/lib/api", () => ({
  useTechnicalsChart: () => ({ data: payload, isLoading: false, isError: false }),
}));

const { ChartPage } = await import("../src/pages/Morning/ChartPage");

function render(url: string, c: TechnicalsChart): string {
  payload = { index: { session: c.session, symbols: [{ symbol: c.symbol, vendor: false }] }, chart: c };
  const html = renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[url]}>
        <ChartPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  // The text as read: React separates adjacent text pieces with empty comments and escapes quotes.
  return html.replace(/<!-- -->/g, "").replace(/&#x27;/g, "'");
}

describe("the technicals chart page", () => {
  it("states that SPX's volume is SPY's, on every setup", () => {
    for (const setup of ["trend", "breakout"]) {
      const html = render(`/charts/technicals?symbol=SPX&setup=${setup}`, chart());
      expect(html).toContain("Volume is SPY");
      expect(html).toContain("nothing trades as SPX");
    }
  });

  it("says nothing about a stand-in where the volume is the name's own", () => {
    const html = render("/charts/technicals?symbol=AAPL", chart({ symbol: "AAPL", volumeSource: null }));
    expect(html).not.toContain("Volume is");
  });

  it("opens on the first setup, shows the one the URL names, and an open position", () => {
    const first = render("/charts/technicals?symbol=SPX", chart());
    expect(first).toContain("first close under the 21 EMA");
    expect(first).toContain("Price, RSI and trend");
    expect(first).toContain("RSI 14 with 30, 50 and 70");
    expect(first).not.toContain("CCI 14");
    const html = render("/charts/technicals?symbol=SPX&setup=breakout", chart());
    expect(html).toContain("Supertrend(10, 3)");
    expect(html).toContain("open since 2026-10-01");
  });

  it("opens on the vendor's view where the vendor was captured, every level where it was not", () => {
    const vendor = {
      capture: "2026-10-02",
      fetchedAt: null,
      through: "2026-10-02",
      levels: [
        { kind: "support", value: 99, date: "2026-09-30", onOurGrid: true, vendorView: true },
        { kind: "gapSupport", value: 100, date: "2026-10-01", onOurGrid: true, vendorView: false },
      ],
      rank: null,
      sentiment: null,
      ivRank: null,
      trendShort: [],
      trendLong: [],
      barsCompared: null,
      barsAgree: null,
    };
    const captured = render("/charts/technicals?symbol=SPX", chart({ vendor }));
    expect(captured).toMatch(/class="mode-btn active">Vendor's view<\/button>/);
    expect(captured).toContain("On their chart");
    const uncaptured = render("/charts/technicals?symbol=SPX", chart());
    expect(uncaptured).toContain('class="mode-btn active">All levels</button>');
    expect(uncaptured).not.toContain("Vendor's view</button>");
    // A link asking for the vendor's view on a name with no capture falls back rather than drawing nothing.
    expect(render("/charts/technicals?symbol=SPX&levels=vendor", chart())).toContain('class="mode-btn active">All levels</button>');
    expect(render("/charts/technicals?symbol=SPX&levels=off", chart({ vendor }))).toContain('class="mode-btn active">Off</button>');
  });

  it("a chart file from before the setups says so rather than drawing nothing silently", () => {
    expect(render("/charts/technicals?symbol=SPX", chart({ setups: [], setupLines: {} }))).toContain("predates the entry/exit setups");
  });
});
