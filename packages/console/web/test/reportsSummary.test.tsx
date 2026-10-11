import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { TradeSummaryPayload, TradeSummarySlice } from "@console/shared";

/**
 * The Reports summary page reads `useTradeSummary` — the one-read endpoint
 * (readers/summary.ts, "The Reports summary's one read"). The tests mock that hook (the
 * meicSummary pattern: renderToString over a mocked query hook) and pin what the CONTRACT
 * promises:
 *
 *   - the payload's own figures survive to the DOM verbatim — the page derives nothing beyond
 *     presentation arithmetic (win-rate ring counts green sessions off the daily rows);
 *   - a module whose slice failed states its refusal, never a wall of zeros;
 *   - `module=all` renders one comparison card per module — never a pooled figure;
 *   - the ROR denominator is named by the payload's own label ("cash deployed" for contango),
 *     not a page-side module table.
 */

const { useTradeSummaryMock } = vi.hoisted(() => ({ useTradeSummaryMock: vi.fn() }));
vi.mock("../src/lib/api", () => ({
  useTradeSummary: useTradeSummaryMock,
}));

const { SummaryPage } = await import("../src/pages/Reports/SummaryPage");

/** Render at a URL — the page's module/mode/range filters ride the URL like every page's. */
const html = (node: React.ReactElement, url = "/reports/summary") =>
  renderToString(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[url]}>{node}</MemoryRouter>
    </QueryClientProvider>,
  ).replace(/<!--\s*-->/g, "");

/** The page's TEXT as a reader sees it — tags stripped, for assertions that span elements. */
const text = (page: string) => page.replace(/<[^>]+>/g, "");

function slice(m: string, over: Partial<TradeSummarySlice> = {}): TradeSummarySlice {
  return {
    module: m,
    mode: "paper",
    schema: "test_schema",
    window: { from: "2026-10-09", to: "2026-10-09" },
    hero: { trades: 10, sessions: 1, gross: 1938.75, fees: 512.87, net: 1401.88, wins: 8, winPct: 80 },
    return: { capitalAtRisk: 7561.25, returnOnRiskPct: 18.5, returnOnRiskRawPct: 18.5404, coverage: "10/10" },
    returnLabel: { capital: "capital at risk", note: "the condor's own worst case" },
    byArm: [{ arm: "control", trades: 9, sessions: 1, net: 1231.27, gross: 1700, fees: 468, wins: 8, winPct: 88.9, capitalCoverage: "9/9", return: 18.2715 }],
    daily: [{ session: "2026-10-09", net: 1401.88, trades: 10, wins: 8 }],
    capture: { value: 72.3, n: 10 },
    facets: { sessions: ["2026-10-08", "2026-10-09"], symbols: ["SPX"], arms: ["control", "bp-1000"] },
    ...over,
  };
}

function payload(over: Partial<TradeSummaryPayload> = {}): TradeSummaryPayload {
  return {
    requested: null,
    mode: "paper",
    range: "day",
    session: null,
    symbol: null,
    arm: null,
    era: { key: "suite", from: null },
    slices: [
      slice("meic"),
      slice("contango", {
        schema: "contango_etf",
        hero: { trades: 2, sessions: 2, gross: 40, fees: 1.02, net: 38.98, wins: 1, winPct: 50 },
        return: { capitalAtRisk: 10000, returnOnRiskPct: 0.38, returnOnRiskRawPct: 0.3898, coverage: "2/2" },
        returnLabel: { capital: "cash deployed", note: "cash deployed in shares" },
        daily: [
          { session: "2026-10-08", net: -20, trades: 1, wins: 0 },
          { session: "2026-10-09", net: 58.98, trades: 1, wins: 1 },
        ],
      }),
    ],
    modulesRead: ["meic", "contango"],
    moduleErrors: {},
    modulesHidden: [],
    returnLabels: {
      meic: { capital: "capital at risk", note: "the condor's own worst case" },
      contango: { capital: "cash deployed", note: "cash deployed in shares" },
    },
    ...over,
  };
}

beforeEach(() => {
  useTradeSummaryMock.mockReset();
  useTradeSummaryMock.mockReturnValue({ data: payload(), isLoading: false, isError: false, error: null });
});

describe("the Reports summary page", () => {
  it("renders the payload's own figures verbatim in the single-module view", () => {
    useTradeSummaryMock.mockReturnValue({ data: payload({ requested: "meic", slices: [slice("meic")] }), isLoading: false, isError: false, error: null });
    const page = html(<SummaryPage />, "/reports/summary?module=meic");
    const t = text(page);
    expect(t).toContain("+$1,401.88"); // the hero net, whole-position dollars
    expect(t).toContain("18.5%"); // the ROR gauge's UNclamped label
    expect(t).toContain("10/10"); // the settled-row coverage, so 9/10 is never read as 10/10
    expect(t).toContain("$7,561.25"); // the denominator beside the value
  });

  it("names the ROR denominator with the payload's own label, per module", () => {
    useTradeSummaryMock.mockReturnValue({
      data: payload({
        requested: "contango",
        slices: [slice("meic", { error: undefined }), slice("contango")],
      }),
      isLoading: false,
      isError: false,
      error: null,
    });
    const page = html(<SummaryPage />, "/reports/summary?module=contango");
    const t = text(page);
    expect(t).toContain("cash deployed");
    expect(t).not.toContain("capital at risk"); // contango's card says what ITS denominator is
  });

  it("a failed slice stays audible: the count chip names how many, the comparison card says why", () => {
    useTradeSummaryMock.mockReturnValue({
      data: payload({
        requested: "meic",
        slices: [slice("meic"), slice("calendars", { error: "no store — the module never ran here" })],
        moduleErrors: { calendars: "no store — the module never ran here" },
      }),
      isLoading: false,
      isError: false,
      error: null,
    });
    // The single-module view carries the count (the detail rides its title attribute).
    const single = text(html(<SummaryPage />, "/reports/summary?module=meic"));
    expect(single).toContain("1 module unreadable");
    expect(single).toContain("+$1,401.88"); // meic still renders
    // The comparison view carries the refusal itself on the module's own card.
    const all = text(html(<SummaryPage />));
    expect(all).toContain("no store — the module never ran here");
    expect(all).toContain("+$1,401.88");
  });

  it("module=all is a comparison: one card per module, no pooled denominator", () => {
    const page = html(<SummaryPage />);
    const t = text(page);
    expect(t).toContain("meic");
    expect(t).toContain("contango");
    expect(t).toContain("+$1,401.88");
    expect(t).toContain("+$38.98");
    expect(t).toContain("cash deployed");
    expect(t).toContain("capital at risk");
  });

  it("modules the switchboard turned off hold no comparison card — one muted line instead", () => {
    useTradeSummaryMock.mockReturnValue({
      data: payload({
        slices: [slice("meic"), slice("contango")],
        modulesHidden: ["calendars", "pmcc", "curve", "contango"],
        moduleErrors: {},
      }),
      isLoading: false,
      isError: false,
      error: null,
    });
    const page = html(<SummaryPage />);
    const t = text(page);
    expect(t).toContain("4 modules off: calendars, pmcc, curve, contango");
    // The comparison still renders the modules that run.
    expect(t).toContain("+$1,401.88");
  });

  it("a zero-trade slice says no rows — never a zero that reads as traded-and-broke-even", () => {
    useTradeSummaryMock.mockReturnValue({
      data: payload({
        requested: "earnings",
        slices: [slice("earnings", { hero: { trades: 0, sessions: 0, gross: 0, fees: 0, net: 0, wins: 0, winPct: null } })],
      }),
      isLoading: false,
      isError: false,
      error: null,
    });
    const page = html(<SummaryPage />, "/reports/summary?module=earnings");
    const t = text(page);
    expect(t).toContain("no rows in this window");
  });
});
