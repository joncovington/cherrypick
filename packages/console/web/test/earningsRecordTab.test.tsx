import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { EarningsRecordPayload } from "@console/shared";

/**
 * The earnings record tab: the running summary, and each spread's money in the suite's layout --
 * signed entry and exit, gross, fees and slippage as their own columns, net -- and an empty record
 * saying when one arrives.
 */

let payload: EarningsRecordPayload = {
  asOf: "2026-10-23",
  summary: {
    announcements: 3,
    measuredMoves: 3,
    compared: 2,
    earningsWeek: { maxDays: 7, n: 1, insideEm: 0, medianMoveOverEm: 1.2 },
    insideEm: 0.5,
    medianMoveOverEm: 0.875,
    strongReads: 2,
    directionRight: 0.5,
    react: { n: 1, net: 130.3, won: 1, returnOnRisk: 0.4311 },
    day2: { n: 0, net: 0, won: null, returnOnRisk: null },
  },
  rows: [
    {
      symbol: "NFLX",
      company: "Netflix, Inc.",
      date: "2026-10-20",
      calendarDay: "2026-10-19",
      timing: "amc",
      reactionDay: "2026-10-21",
      edge: "sbe",
      edgeLabel: "strong bear",
      liquidityRank: 1,
      region: "US",
      movePct: -10,
      emMovePct: 11,
      eventExpiry: "2026-11-20",
      eventExpiryDays: 30,
      moveOverEm: 0.875,
      insideEm: true,
      directionRight: true,
      spread: {
        state: "entered",
        kind: "credit",
        right: "C",
        expiry: "2026-11-20",
        strikes: [75, 70],
        entryMid: 2,
        liquidAtEntry: true,
        react: { entry: 200, exit: -60, gross: 140, fees: 4.5, slippage: 5.2, net: 130.3, risk: 302.25 },
        day2: null,
      },
    },
  ],
};

vi.mock("../src/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/lib/api")>()),
  useEarningsRecord: () => ({ data: payload, isLoading: false, isError: false }),
}));

const { EarningsRecordTab } = await import("../src/pages/Screeners/EarningsRecordTab");

function render(): string {
  return renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <EarningsRecordTab />
      </MemoryRouter>
    </QueryClientProvider>,
  )
    .replace(/<!-- -->/g, "")
    .replace(/&#x27;/g, "'");
}

describe("the earnings record tab", () => {
  it("shows the running summary", () => {
    const html = render();
    expect(html).toContain("3 of 3 measured · 2 with an expected move to compare · inside it 50% · median |move| 0.88× it");
    expect(html).toContain("priced within 7 days of the reaction: 1 · inside 0% · median 1.20×");
    expect(html).toContain("to Nov 20 +30d");
    expect(html).toContain(">month's move<");
    expect(html).toContain("direction called 50% of 2 strong reads");
    expect(html).toMatch(/reaction day: 1 spreads · net <span class="pnl-pos">\+\$130\.30<\/span> · won 100% · RoR 43\.1%/);
  });

  it("states a spread's money in the suite's layout", () => {
    const html = render();
    // entry +200 (a credit received), exit −60, gross +140, fees and slippage as costs, net.
    for (const cell of ["+$200.00", "−$60.00", "+$140.00", "−$4.50", "−$5.20", "+$130.30"]) expect(html).toContain(cell);
  });

  it("an empty record says when one arrives", () => {
    payload = { asOf: null, summary: null, rows: [] };
    expect(render()).toContain("No record yet");
  });
});
