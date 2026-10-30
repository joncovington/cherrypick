import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { BwbPayload, TradingMode } from "@console/shared";
import { BwbSession } from "../src/pages/Bwb/BwbSession";
import { OpenTradesCard } from "../src/pages/Bwb/CurrentStateCards";

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
    const head = html.slice(html.indexOf("<thead>"), html.indexOf("</thead>"));
    for (const h of ["symbol", "arm", "opened → expiry", "entry", "add-on $", "mark (net of costs to date)"]) expect(head).toContain(h);
    for (const h of ["qty", "spot", "peak |delta|"]) expect(head).not.toContain(`>${h}<`);
    expect(html).toContain("below flip"); // the flag rides the symbol cell
    expect(html).not.toContain("0.123"); // peak delta waits for the expanded line
  });
});
