import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { BwbPayload, TradingMode } from "@console/shared";
import { BwbSession } from "../src/pages/Bwb/BwbSession";
import { BWB_OPEN_SORTS, OpenTradesCard } from "../src/pages/Bwb/CurrentStateCards";

/**
 * bwb's session slide under the live toggle. The live ledger exists but has never held a position
 * (its state since the pilot was built), so the slide must say there is nothing yet -- not draw a
 * row of tiles reading "0 open", "0 fired", "0 entries" that look like a measured quiet day.
 */

function payload(mode: TradingMode): BwbPayload {
  return {
    mode,
    session: null,
    dbPresent: true,
    openPositions: [],
    openCount: 0,
    arms: [],
    fireCounts: [],
    correlationCaveat: "",
    entryAttemptsToday: [],
    managementEventsToday: [],
    integrity: {
      triggerCoverage: {
        session: null,
        ticks: 0,
        refused: 0,
        refusalShare: null,
        noSpot: 0,
        noFlip: 0,
        reasons: {},
        totalFailure: false,
      },
      markCoverage: { session: null, marks: 0, refused: 0, refusalShare: null },
      schemaDrift: [],
      measurementBreaks: [],
    },
    today: { lastIteration: null },
  };
}

const render = (mode: TradingMode) =>
  renderToString(
    <MemoryRouter>
      <BwbSession data={payload(mode)} loading={false} mode={mode} />
    </MemoryRouter>,
  );

describe("BwbSession on an empty live ledger", () => {
  it("says there are no live trades yet, and draws no tiles", () => {
    const html = render("live");
    expect(html).toContain("No live trades yet");
    expect(html).not.toContain("open positions");
    expect(html).not.toContain("add-ons fired");
  });

  it("leaves the paper slide's tiles alone", () => {
    const html = render("paper");
    expect(html).not.toContain("No live trades yet");
    expect(html).toContain("open positions");
  });
});

describe("BWB open trades, a short row", () => {
  const position = {
    positionId: "p1", symbol: "SPX", arm: "bounce", status: "open", bodyStrike: 6800, nearStrike: 6850, farStrike: 6700,
    expiration: "2026-11-06", entrySpot: 6900, entryCredit: 0.95, entryMaxLoss: 6000, quantity: 1, entryCash: 95, addOnCash: null,
    peakAbsDelta: 0.123, belowFlipSeen: true, armedAt: null, addonFiredAt: null, addonShortStrike: null, addonLongStrike: null,
    addonCredit: null, currentCloseCost: null, currentSpot: 6880, entrySession: "2026-10-30", unrealisedGross: 40, unrealisedNet: 35, feesToDate: 5,
  };

  it("keeps the trade and its money in the row, with the rest in the expanded line (2026-10-30)", () => {
    const html = renderToString(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <OpenTradesCard data={{ ...payload("paper"), openPositions: [position], openCount: 1 } as unknown as BwbPayload} />
        </MemoryRouter>
      </QueryClientProvider>,
    ).replace(/<!-- -->/g, "");
    // A trade card, in the order-chain style: the fly's three strikes, the body twice.
    expect(html).not.toContain("<thead>");
    expect(html).toContain("pos-card");
    expect(html).toContain("1 6850P");
    expect(html).toContain("2 6800P");
    expect(html).toContain("1 6700P");
    expect(html.match(/is-buy/g)?.length).toBe(2);
    expect(html.match(/is-sell/g)?.length).toBe(1);
    expect(html).toContain("below flip");
    expect(html).toContain("marked net $35.00");
    expect(html).toContain("peak |delta| 0.123");
  });

  it("offers sorts on timing, size and fit only -- never credit or yield", () => {
    const allowed = ["time", "expiry", "size", "arm"];
    expect(BWB_OPEN_SORTS.map((s) => s.id)).toEqual(allowed);
    for (const s of BWB_OPEN_SORTS) expect(`${s.id} ${s.label}`).not.toMatch(/credit|yield|theta/i);
  });

  it("shows the add-on's strikes as their own legs once it has fired", () => {
    const fired = { ...position, addonFiredAt: "2026-10-30T15:00:00", addonShortStrike: 6650, addonLongStrike: 6625, addOnCash: 40 };
    const html = renderToString(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <OpenTradesCard data={{ ...payload("paper"), openPositions: [fired], openCount: 1 } as unknown as BwbPayload} />
        </MemoryRouter>
      </QueryClientProvider>,
    ).replace(/<!-- -->/g, "");
    expect(html).toContain("1 6650P add-on");
    expect(html).toContain("1 6625P add-on");
    expect(html).toContain("put BWB + add-on");
  });
});
