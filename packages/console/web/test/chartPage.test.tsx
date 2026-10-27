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
      { id: "trend", name: "Trend following", family: "trend", side: "long", rule: "Exit on the first close under the 21 EMA.", lines: ["ema21"], trades: [] },
      {
        id: "breakout",
        name: "Breakout",
        family: "breakout",
        side: "long",
        rule: "Exit on the first close with Supertrend(10, 3) down.",
        lines: ["supertrend"],
        trades: [{ entryDate: "2026-10-01", entryPrice: 101.5, exitDate: null, exitPrice: null, reason: null, target: null }],
      },
    ],
    ourLevels: [],
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

  it("opens on our own levels and never shows the vendor's frozen capture; an old vendor link falls back", () => {
    const vendor = {
      capture: "2026-10-02",
      fetchedAt: null,
      through: "2026-10-02",
      levels: [
        { kind: "support", value: 99, date: "2026-09-30", onOurGrid: true, vendorView: true },
        { kind: "gapSupport", value: 100, date: "2026-10-01", onOurGrid: true, vendorView: false },
      ],
      rank: 7,
      sentiment: "Bearish",
      ivRank: 55,
      trendShort: [{ date: "2026-10-01", value: 3 }],
      trendLong: [],
      barsCompared: 10,
      barsAgree: 10,
    };
    const ourLevels = [{ kind: "support" as const, value: 98, date: "2026-09-30" }];
    // Ours whenever the file has them; a file still carrying the vendor's capture shows none of it
    // (the subscription ended 2026-10-24, and a frozen capture beside today's bars reads as current).
    const ours = render("/charts/technicals?symbol=SPX", chart({ vendor, ourLevels }));
    expect(ours).toContain('class="mode-btn active">Our levels</button>');
    for (const gone of ["Against the vendor", "On their chart", "vendor rank", "vendor: Bearish", "Vendor&#x27;s view", "trend (vendor)", "vendor captured"]) {
      expect(ours).not.toContain(gone);
    }
    // A file from before our levels falls back to our grid's extremes, captured or not.
    expect(render("/charts/technicals?symbol=SPX", chart({ vendor }))).toContain('class="mode-btn active">With grid</button>');
    expect(render("/charts/technicals?symbol=SPX", chart())).toContain('class="mode-btn active">With grid</button>');
    // A saved link asking for the retired vendor's view falls back rather than drawing nothing.
    expect(render("/charts/technicals?symbol=SPX&levels=vendor", chart({ vendor, ourLevels }))).toContain('class="mode-btn active">Our levels</button>');
    expect(render("/charts/technicals?symbol=SPX&levels=off", chart({ vendor }))).toContain('class="mode-btn active">Off</button>');
  });

  it("shows a family's short when the side asks for it, and a short's own id picks its side", () => {
    const short = { id: "trend-short", name: "Trend following (short)", family: "trend", side: "short" as const, rule: "Cover on the first close over the 21 EMA.", lines: ["ema21"], trades: [] };
    const c = chart({ setups: [...chart().setups, short] });
    expect(render("/charts/technicals?symbol=SPX", c)).toContain('class="mode-btn active">Long</button>');
    const shortSide = render("/charts/technicals?symbol=SPX&setup=trend&side=short", c);
    expect(shortSide).toContain("Cover on the first close over the 21 EMA.");
    expect(shortSide).not.toContain("Exit on the first close under the 21 EMA.");
    expect(render("/charts/technicals?symbol=SPX&setup=trend-short", c)).toContain('class="mode-btn active">Short</button>');
    const both = render("/charts/technicals?symbol=SPX&setup=trend&side=both", c);
    expect(both).toContain("Long: ");
    expect(both).toContain("Short: ");
  });

  it("a chart file from before the setups says so rather than drawing nothing silently", () => {
    expect(render("/charts/technicals?symbol=SPX", chart({ setups: [], setupLines: {} }))).toContain("predates the entry/exit setups");
  });
});
