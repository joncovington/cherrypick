import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";

/**
 * Which headers of the server-paged history tables sort (2026-10-16): MEIC's history and positions,
 * the flies trade log and the earnings history. Each is a page of a server result, so a header may
 * sort only where the server orders the whole result by it -- the lists are held to the readers'
 * accessors by server/test/history-sort-readers.test.ts. Rendered with no rows: the headers are
 * the contract here, not the cells.
 */

vi.mock("../src/lib/prefs", () => ({
  useBoolPref: () => false,
  usePrefsSync: () => undefined,
  usePrefsVersion: () => 0,
  writePref: async () => undefined,
  getPref: () => undefined,
}));

const empty = { rows: [], total: 0, offset: 0, limit: 100 };
vi.mock("../src/lib/api", async (orig) => ({
  ...(await orig<typeof import("../src/lib/api")>()),
  useMeic: () => ({ data: { trades: empty, totals: undefined }, isLoading: false, isError: false, isPlaceholderData: false }),
  useFliesTradeLog: () => ({ data: { ...empty, totals: undefined }, isLoading: false, isError: false, isPlaceholderData: false }),
}));

const { MeicHistoryTable, MeicPositionsTable, MEIC_TRADE_SORTS } = await import("../src/pages/Meic/MeicTables");
const { EarningsHistory, HISTORY_SORTS } = await import("../src/pages/Earnings/EarningsHistory");
const { HistoryTab } = await import("../src/pages/Flies/HistoryTab");

function render(el: ReactElement): string {
  return renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{el}</MemoryRouter>
    </QueryClientProvider>,
  ).replace(/<!-- -->/g, "");
}
const buttons = (html: string) => [...html.matchAll(/<button type="button" class="th-sort">([^<]*)/g)].map((m) => m[1]);

const scope = { day: null, symbol: null, profile: null, era: null };

describe("server-sorted history headers", () => {
  it("MEIC history offers every column its reader sorts, and nothing else", () => {
    const sortable = buttons(render(<MeicHistoryTable mode="paper" scope={scope} reasons={[]} />));
    expect(sortable).toEqual(["date", "time", "sym", "put", "call", "wing", "qty", "price", "entry", "exit", "gross", "fees", "settle", "slip", "net"]);
    expect(sortable.every((h) => (MEIC_TRADE_SORTS as readonly string[]).includes(h!))).toBe(true);
  });

  it("MEIC positions sort through the same reader; IVR and status stay plain", () => {
    expect(buttons(render(<MeicPositionsTable mode="paper" scope={scope} />))).toEqual([
      "time", "sym", "put", "call", "wing", "qty", "price", "entry",
    ]);
  });

  it("earnings history sorts its reader's columns once the page can toggle the sort", () => {
    const page = { offset: 0, limit: 100, setOffset: () => {}, setLimit: () => {} };
    expect(buttons(render(<EarningsHistory data={undefined} loading={false} isError={false} busy={false} ranged={false} page={page} />))).toEqual([]);
    const sortable = buttons(
      render(
        <EarningsHistory data={undefined} loading={false} isError={false} busy={false} ranged={false} page={{ ...page, toggleSort: () => {} }} />,
      ),
    );
    expect(sortable).toEqual(["opened", "closed", "sym", "strategy", "qty", "price", "entry", "exit", "gross", "fees", "settle", "slip", "net"]);
    expect(sortable).toEqual(HISTORY_SORTS.map((id) => id));
  });

  it("the flies trade log sorts its money and identity columns; kind, wing, window, how and pinned stay plain", () => {
    const html = render(
      <HistoryTab mode="paper" filter={{ arm: null, date: null, symbol: null, era: null }} onReplayDay={() => {}} />,
    );
    const sortable = buttons(html);
    // The tab's summary cards sort too, so the log's own headers are the ones only it carries.
    for (const h of ["date", "time", "sym", "centre", "latency", "price", "entry", "exit", "slip"]) expect(sortable).toContain(h);
    for (const h of ["kind", "wing", "window", "how", "pinned"]) expect(sortable).not.toContain(h);
  });
});
