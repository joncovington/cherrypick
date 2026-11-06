import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ManageTimeSpreads, TimeSpreadStructure } from "@console/shared";
import { TimeSpreadsCard } from "../src/pages/Live/TimeSpreadsCard";

/**
 * The time-spread card draws manage's rows and decides nothing; it always says there is no tested
 * edge, and says when bullish rows are held back (made-up figures).
 */

function render(t: ManageTimeSpreads | null): string {
  return renderToString(<TimeSpreadsCard t={t} />).replace(/<!-- -->/g, "");
}

const ROW: TimeSpreadStructure = {
  underlying: "XYZ",
  kind: "put calendar",
  direction: "neutral",
  label: "put calendar 2026-11-13/2026-12-18 100/100P",
  front: "2026-11-13",
  back: "2026-12-18",
  spot: 100.5,
  ratio: 1.3,
  ivRank: 0.5,
  sector: "Tech",
  debitMid: 2.56,
  debitNatural: 2.7,
  gap: null,
  debitShareOfGap: null,
  frontIv: 0.6,
  backIv: 0.45,
  netDelta: 1.2,
  thetaDay: 9.5,
  pnlFlat: 120,
  pnlUp5: -40,
  pnlDown10: -150,
  losesUnchanged: false,
  breakeven: [94.1, 106.2],
  priceStop: [91, null],
  takeProfit: 3.2,
  valueStop: 1.28,
  rollBy: "2026-11-10",
  maxLoss: 256,
  units: 2,
  binding: null,
  heldName: false,
  heldSector: true,
  bpAfterPct: 0.35,
};

const BASE: ManageTimeSpreads = {
  sourceSession: "2026-11-06",
  takenAt: null,
  order: "ordered by the front/back IV ratio: a hypothesis, untested (item 54)",
  edge: "no tested edge",
  deltaFilter: { bookBwd: 40, limit: 100, bullishHeldBack: false },
  bpCapPct: 0.6,
  fits: [ROW],
  fitsTotal: 1,
  tooBig: [],
  tooBigTotal: 0,
  dropped: {},
  error: null,
};

describe("TimeSpreadsCard", () => {
  it("draws nothing for an evaluation without the rows", () => {
    expect(render(null)).toBe("");
  });

  it("says there is no tested edge and draws the row with its levels", () => {
    const html = render(BASE);
    expect(html).toContain("No tested edge.");
    expect(html).toContain("put calendar 2026-11-13/2026-12-18 100/100P");
    expect(html).toContain("$91.00 / none");
    expect(html).toContain("sector held");
    expect(html).not.toContain("held back");
  });

  it("says when bullish structures are held back", () => {
    const html = render({ ...BASE, deltaFilter: { bookBwd: 150, limit: 100, bullishHeldBack: true } });
    expect(html).toContain("bullish structures held back");
  });

  it("shows the reading's error instead of rows", () => {
    expect(render({ ...BASE, error: "no time-spread reading today", fits: [] })).toContain("no time-spread reading today");
  });
});
