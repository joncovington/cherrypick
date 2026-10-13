import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { LiveBooksPayload } from "@console/shared";
import { binTradeNets, TradeNetHistogram } from "../src/components/performance/TradeNetHistogram";
import { LivePanel } from "../src/pages/Overview/LivePanel";

/**
 * The per-trade net histogram draws `tradeNets` as served -- binning only, with zero always a bin
 * edge so no bar mixes winners and losers -- and the Overview's live panel draws only the live books
 * with activity, naming the quiet ones rather than dropping them.
 */

describe("binTradeNets", () => {
  it("counts every trade once and never lets a bin straddle zero", () => {
    const nets = [-120.5, -40, -3.2, 0, 4.99, 18, 62.5, 62.5, 210];
    const bins = binTradeNets(nets);
    expect(bins.reduce((s, b) => s + b.n, 0)).toBe(nets.length);
    for (const b of bins) expect(b.lo < 0 && b.hi > 0).toBe(false);
    expect(bins[0]!.lo).toBeLessThanOrEqual(-120.5);
    expect(bins[bins.length - 1]!.hi).toBeGreaterThan(210);
  });

  it("is empty for no trades and draws nothing", () => {
    expect(binTradeNets([])).toEqual([]);
    expect(renderToString(<TradeNetHistogram nets={[]} />)).toBe("");
  });

  it("labels the count above and below zero", () => {
    const html = renderToString(<TradeNetHistogram nets={[-5, 10, 20]} />);
    expect(html).toContain("3<!-- --> trades");
    expect(html).toContain("2<!-- --> above zero, <!-- -->1<!-- --> below");
  });
});

function renderPanel(payload: LiveBooksPayload): string {
  const client = new QueryClient();
  client.setQueryData(["live-books"], payload);
  return renderToString(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <LivePanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const quiet = {
  ledger: "ok" as const,
  error: null,
  positions: 0,
  open: 0,
  latestSession: null,
  latestNet: null,
  latestNetSession: null,
  totalNet: null,
};

describe("the Overview's live panel", () => {
  it("draws only the books with activity and names the quiet ones", () => {
    const html = renderPanel({
      books: [
        { module: "flies", to: "/live/today", ...quiet },
        { module: "bwb", to: "/bwb/livegap", ...quiet, positions: 3, open: 2, latestSession: "2026-10-20", latestNet: -45.5, latestNetSession: "2026-10-16", totalNet: 120.25 },
      ],
    });
    expect(html).toContain("live bwb");
    expect(html).toContain("-$45.50");
    expect(html).toContain("$120.25");
    expect(html).toContain("flies: no live activity yet");
    expect(html).not.toContain("live-flies-panel");
  });

  it("says so when no module has live activity, and shows a failed read as a failure", () => {
    const html = renderPanel({
      books: [
        { module: "flies", to: "/live/today", ...quiet, ledger: "absent" },
        { module: "bwb", to: "/bwb/livegap", ...quiet, ledger: "failed", error: "no such column" },
      ],
    });
    expect(html).toContain("No module has live activity");
    expect(html).toContain("flies: no live ledger on this machine");
    expect(html).toContain("could not be read (no such column)");
  });
});
