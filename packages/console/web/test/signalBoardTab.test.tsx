import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { SignalBoardPayload, SignalBoardRow } from "@console/shared";

/**
 * The signal board tab: two lists in the file's order, a minimum count (2 by default) with its
 * escapes, a tie never listed, approximate methods marked, and the note naming what is not counted.
 */

function row(over: Partial<SignalBoardRow>): SignalBoardRow {
  return { symbol: "AAA", sector: "Technology", side: "bullish", count: 2, bullish: 2, bearish: 0, tags: [], ...over };
}

const payload: SignalBoardPayload = {
  day: "2026-10-16",
  days: ["2026-10-16"],
  names: 382,
  methods: {
    stage: { label: "RS stage", approximate: false },
    rotation: { label: "Sector rotation", approximate: true },
  },
  notCounted: ["fundamentals", "put buying"],
  revisionsThrough: "2026-10-17T22:11:24+00:00",
  rows: [
    row({
      symbol: "CAH",
      count: 3,
      bullish: 3,
      tags: [
        { method: "stage", label: "Confirmed outperformer", detail: "" },
        { method: "rotation", label: "Money rotating into the sector", detail: "XLV leading" },
      ],
    }),
    row({ symbol: "AAL", side: "bearish", sector: "Industrials", bullish: 0, bearish: 2 }),
    row({ symbol: "ONE", count: 1, bullish: 1 }),
    row({ symbol: "TIE", side: null, count: 1, bullish: 1, bearish: 1 }),
  ],
};

vi.mock("../src/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/lib/api")>()),
  useSignalBoard: () => ({ data: payload, isLoading: false, isError: false }),
}));

const { SignalBoardTab } = await import("../src/pages/Screeners/SignalBoardTab");

function render(url: string): string {
  return renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[url]}>
        <SignalBoardTab />
      </MemoryRouter>
    </QueryClientProvider>,
  )
    .replace(/<!-- -->/g, "")
    .replace(/&#x27;/g, "'");
}

const symbols = (html: string) => [...html.matchAll(/symbol=([A-Z]+)"/g)].map((m) => m[1]);

describe("the signal board tab", () => {
  it("lists bullish then bearish at two or more by default, and never a tie", () => {
    expect(symbols(render("/screeners/board"))).toEqual(["CAH", "AAL"]);
    expect(symbols(render("/screeners/board?min=1"))).toEqual(["CAH", "ONE", "AAL"]);
    expect(symbols(render("/screeners/board?min=3"))).toEqual(["CAH"]);
    expect(symbols(render("/screeners/board?min=1&sector=Industrials"))).toEqual(["AAL"]);
  });

  it("marks an approximate method and names what it does not count", () => {
    const html = render("/screeners/board");
    expect(html).toContain(">≈ Money rotating into the sector</span>");
    expect(html).toContain(">Confirmed outperformer</span>");
    expect(html).toContain("Approximate: our engine differs");
    expect(html).toContain("Not counted yet: fundamentals, put buying");
    expect(html).toContain("not a tested signal");
  });
});
