import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { BwbPayload, TradingMode } from "@console/shared";
import { BwbSession } from "../src/pages/Bwb/BwbSession";

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
