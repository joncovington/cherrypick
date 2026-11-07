import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { CalendarsPosition } from "@console/shared";
import { CALENDAR_SORTS, PositionsCard, calendarPositionModel } from "../src/pages/Calendars/WeekCards";
import { HelpTab } from "../src/pages/Calendars/HelpTab";

/**
 * The calendars module's own tabs in the portfolio tab's layout (2026-11-07): the held structures are
 * trade cards in the order-chain style (the front short, the back long), sorted on timing and size
 * only, and the help tab is questions with the text unchanged.
 */

const position = {
  positionId: "c1", weekOf: "2026-11-02", entrySession: "2026-11-02", arm: "control", side: "put", symbol: "XYZ", structure: "dc_4_7",
  frontExpiration: "2026-11-06", backExpiration: "2026-11-09", strike: 100, quantity: 2, entryDebit: 1.5, entryCash: -300, entrySpot: 101,
  entryEm: 3, entryEmPct: 0.03, entryFrontIv: 0.2, entryBackIv: 0.22, entryTermStructure: 1.1, status: "open", exitReason: null,
  closedSession: null, settlementSpot: null, itmSettlements: null, grossPnl: null, fees: null, netPnl: null, unrealisedGross: 20, unrealisedNet: 15,
  feesToDate: 5,
  legs: [
    { legRole: "front", occSymbol: "x", expiration: "2026-11-06", strike: 100, optionType: "put", action: "Sell to Open", entryMid: 1, status: "open", closeKind: null, closeValue: null },
    { legRole: "back", occSymbol: "y", expiration: "2026-11-09", strike: 100, optionType: "put", action: "Buy to Open", entryMid: 2.5, status: "open", closeKind: null, closeValue: null },
  ],
} as unknown as CalendarsPosition;

describe("calendars structures", () => {
  it("draws a structure as a trade card with the front short and the back long", () => {
    const html = renderToString(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <PositionsCard title="structures this week" positions={[position]} emptyText="none" />
        </MemoryRouter>
      </QueryClientProvider>,
    ).replace(/<!-- -->/g, "");
    expect(html).toContain("pos-card");
    expect(html).not.toContain("<thead>");
    expect(html).toContain("2 100P 6 Nov");
    expect(html).toContain("2 100P 9 Nov");
    expect(html.match(/is-sell/g)?.length).toBe(1);
    expect(html.match(/is-buy/g)?.length).toBe(1);
    expect(html).toContain("marked net $15.00");
  });

  it("reads a settled week's net as realised", () => {
    const m = calendarPositionModel({ ...position, status: "closed", netPnl: -40 });
    expect(m.label).toContain("realised");
    expect(m.label).toContain("40.00");
  });

  it("sorts on timing, size and fit only", () => {
    expect(CALENDAR_SORTS.map((s) => s.id)).toEqual(["time", "expiry", "size", "arm"]);
    for (const s of CALENDAR_SORTS) expect(`${s.id} ${s.label}`).not.toMatch(/credit|yield|theta/i);
  });
});

describe("calendars help tab", () => {
  it("asks its questions in order and keeps the text", () => {
    const html = renderToString(<HelpTab data={undefined} />).replace(/<!-- -->/g, "");
    const at = ["What is this module?", "How should the numbers be read?", "What does it run on?", "Where does the data come from?"].map((q) => html.indexOf(q));
    expect(at.every((i) => i >= 0)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    expect(html).toContain("unconditional and mechanical");
    expect(html).not.toContain("cards-wide");
  });
});
