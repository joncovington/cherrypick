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
      symbol: "XYZ",
      company: "Xyz Inc.",
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

const { EarningsRecordTab, JOURNAL_SORTS, MoneyDetail, matches, recordHelp } = await import("../src/pages/Screeners/EarningsRecordTab");
const { sortRows } = await import("../src/components/FilterSort");

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
  it("shows the running summary as tiles, with the no-edge flag and the expiry-mixing bias behind the ?", () => {
    const html = render();
    expect(html).toContain("forward check");
    expect(html).toMatch(/class="help-flag"[^>]*>no edge in history</);
    for (const cell of ["of 3 announcements", "of 2 with an expected move", "0.88", "of 2 strong reads", "+$130.30", "1 spreads · won 100% · RoR 43.1%"]) {
      expect(html).toContain(cell);
    }
    expect(html).toContain("priced within 7 days");
    expect(html).toContain("1.20");
    // The bias note is not on the card; it opens from the ?.
    expect(html).not.toContain("leans toward inside");
    const help = renderToString(<>{recordHelp.body}</>).replace(/<!-- -->/g, "");
    expect(help).toContain("leans toward inside");
  });

  it("lists the journal as rows with a pager and filter and sort, each row's money opening in place", () => {
    const html = render();
    expect(html).toContain("journal");
    expect(html).toContain("1–1 of 1");
    expect(html).toContain('aria-label="Filter and sort"');
    expect(html).toContain("by report date, newest first");
    expect(html).toContain('aria-label="Open XYZ"');
    expect(html).toContain('aria-expanded="false"');
    // Closed: the money columns are not drawn until a row opens.
    expect(html).not.toContain(">slippage<");
    // The net's hover adds up in signed cash flow: costs negative, never "− fees +$4.50".
    expect(html).toContain('title="gross +$140.00 · fees −$4.50 · slippage −$5.20 = net +$130.30"');
  });

  it("states a spread's money in the suite's layout when a row opens", () => {
    const html = renderToString(<MoneyDetail r={payload.rows[0]!} />).replace(/<!-- -->/g, "");
    // entry +200 (a credit received), exit −60, gross +140, fees and slippage as costs, net.
    for (const cell of ["+$200.00", "−$60.00", "+$140.00", "−$4.50", "−$5.20", "+$130.30"]) expect(html).toContain(cell);
    for (const label of [">entry<", ">exit<", ">gross<", ">fees<", ">slippage<", ">net<"]) expect(html).toContain(label);
    expect(html).toContain("credit call 70/75");
  });

  it("sorts by date, the move's size or net, and filters by outcome and direction", () => {
    const base = payload.rows[0]!;
    const rows = [
      { ...base, symbol: "OLD", date: "2026-10-01", moveOverEm: 0.5, insideEm: true, directionRight: false, edge: "sb" },
      { ...base, symbol: "NEW", date: "2026-10-20", moveOverEm: 1.4, insideEm: false, directionRight: true, edge: "sbe" },
    ];
    const by = (id: string, reversed = false) => sortRows(rows, JOURNAL_SORTS.find((s) => s.id === id), reversed).map((r) => r.symbol);
    expect(by("date")).toEqual(["NEW", "OLD"]);
    expect(by("date", true)).toEqual(["OLD", "NEW"]);
    expect(by("move")).toEqual(["NEW", "OLD"]);
    expect(JOURNAL_SORTS.map((s) => s.id)).toEqual(["date", "move", "net"]);
    expect(rows.filter((r) => matches(r, new Set(["outside"]))).map((r) => r.symbol)).toEqual(["NEW"]);
    expect(rows.filter((r) => matches(r, new Set(["called"]))).map((r) => r.symbol)).toEqual(["NEW"]);
    expect(rows.filter((r) => matches(r, new Set(["bull"]))).map((r) => r.symbol)).toEqual(["OLD"]);
    expect(rows.filter((r) => matches(r, new Set(["outside", "bull"])))).toEqual([]);
  });

  it("an empty record says when one arrives", () => {
    payload = { asOf: null, summary: null, rows: [] };
    expect(render()).toContain("No record yet");
  });
});
