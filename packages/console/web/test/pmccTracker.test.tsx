import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { PmccTracker, PmccWeeklyRow } from "@console/shared";
import { TrackerView } from "../src/pages/Pmcc/TrackerTab";
import { WeeklyByArm } from "../src/pages/Pmcc/WeeklyByArmCard";

const text = (node: React.ReactElement) =>
  renderToString(<MemoryRouter>{node}</MemoryRouter>).replace(/<!--\s*-->/g, "");

// The held-long fixture `packages/pmcc/tests/test_tracker.py` works out by hand, as the bridge
// serves it (camelised): three weeks, one Friday roll, every cost at its own time.
const T: PmccTracker = {
  position: {
    positionId: "TQQQ:shield_hold:2026-08-24",
    symbol: "TQQQ",
    arm: "shield_hold",
    era: "shield",
    status: "open",
    exitReason: null,
    entrySession: "2026-08-24",
    closedSession: null,
    quantity: 1,
    entrySpot: 70.6,
    rollCount: 1,
    exposureTicks: 0,
    lifecycle: "held_long",
  },
  header: {
    longCost: -3780,
    longValue: 3850,
    longGain: 70,
    shortRealised: 60,
    shortOpen: 45,
    shares: 0,
    gross: 175,
    costs: { basis: "legs", fees: 3.5, slippage: 11.89, settlement: 0, total: 15.39 },
    net: 159.61,
    returnOnLongCost: 0.0422,
    returnOnNotional: 0.0226,
    underlyingSinceOpen: 0.0113,
    daysInTrade: 16,
    netDelta: 21,
    extrinsicCaptured: 155,
    longExtrinsicDecay: 30,
    netExtrinsic: 125,
    shortsSold: 2,
    notional: 7060,
    exits: { stopNetAt: -1134, stopRoom: 1293.61, longCloseOn: "2027-08-03", longDte: 373 },
  },
  longLots: [
    {
      opened: "2026-08-24",
      expiration: "2027-09-17",
      spotOpen: 70.6,
      strike: 35,
      quantity: 1,
      cost: -3780,
      priceNow: 38.5,
      valueNow: 3850,
      gain: 70,
      extrinsicPaid: 220,
      extrinsicNow: 190,
      deltaNow: 0.95,
      status: "open",
    },
  ],
  currentShort: {
    stockAtSale: 71,
    strike: 70,
    expiration: "2026-09-11",
    premium: 2.45,
    intrinsicAtSale: 1,
    extrinsicAtSale: 145,
    extrinsicNow: 60,
    decayedPct: 0.5862,
    extrinsicOnLongCostPct: 0.0384,
    projected: 145,
    breakeven: 68.55,
    dte: 2,
    next: { expiryRollAt: "2026-09-11 15:00 ET", decayRollBelow: null, breachAt: null },
  },
  shorts: [
    {
      n: 1, legRole: "short_call_1", status: "closed", opened: "2026-08-24", closed: "2026-09-04", daysHeld: 11,
      spotOpen: 70.6, spotClose: 71, strike: 68, expiration: "2026-09-04", sold: 3.65, bought: 3.05,
      entry: 365, exit: -305, how: "rolled", why: "roll:expiry", gross: 60, fees: 1.26, slippage: 1.26, net: 57.48,
      extrinsicSold: 105, extrinsicLeft: 5, extrinsicCaptured: 100,
    },
    {
      n: 2, legRole: "short_call_2", status: "open", opened: "2026-09-04", closed: null, daysHeld: 5,
      spotOpen: 71, spotClose: 71.4, strike: 70, expiration: "2026-09-11", sold: 2.45, bought: 2.0,
      entry: 245, exit: -200, how: "open", why: null, gross: 45, fees: 1.12, slippage: 0.63, net: 43.25,
      extrinsicSold: 145, extrinsicLeft: 60, extrinsicCaptured: 85,
    },
  ],
  weeks: [
    { week: "2026-W35", weekEnd: "2026-08-28", priced: true, spot: 70.9, longMark: 38, shortOpen: 25, shortRealised: 0, costs: 12.87, net: 32.13, change: 32.13, returnOnLongCost: 0.0085, returnOnNotional: 0.0046, netDelta: 14 },
    { week: "2026-W36", weekEnd: "2026-09-04", priced: true, spot: 71, longMark: 38.2, shortOpen: 15, shortRealised: 60, costs: 15.39, net: 99.61, change: 67.48, returnOnLongCost: 0.0264, returnOnNotional: 0.0141, netDelta: 24 },
    { week: "2026-W37", weekEnd: "2026-09-11", priced: true, spot: 71.4, longMark: 38.5, shortOpen: 45, shortRealised: 60, costs: 15.39, net: 159.61, change: 60, returnOnLongCost: 0.0422, returnOnNotional: 0.0226, netDelta: 21 },
  ],
  integrity: { exposureTicks: 0, unpricedWeeks: 0, weeksWithoutAShort: [], costsBasis: "legs" },
  asOf: 0,
};

describe("the PMCC position tracker", () => {
  it("leads with the position's net after every cost, and both ways of stating its return", () => {
    const html = text(<TrackerView t={T} />);
    expect(html).toContain("TQQQ · shield_hold · open");
    expect(html).toContain("+$159.61");
    expect(html).toContain("-$3780.00"); // the long's cost, a debit
    expect(html).toContain("+4.22%"); // on the long's cost, the promoters' way
    expect(html).toContain("+2.26%"); // on the stock value controlled
    expect(html).toContain("held long");
  });

  it("states a held-long position's two exits: the stop's level and room, and the long-roll date", () => {
    const html = text(<TrackerView t={T} />);
    expect(html).toContain("-$1134.00"); // the stop, as a net-to-date level
    expect(html).toContain("$1293.61 away");
    expect(html).toContain("long rolls on");
    expect(html).toContain("2027-08-03");
    const before = { ...T, header: { ...T.header, exits: undefined } } as unknown as typeof T;
    expect(text(<TrackerView t={before} />)).not.toContain("long rolls on"); // a module not yet restarted
  });

  it("states each short in the trade standard's money order, with slippage named as a charged cost", () => {
    const html = text(<TrackerView t={T} />);
    expect(html).toContain("short-call log");
    expect(html).toContain("+$365.00"); // entry: premium received
    expect(html).toContain("-$305.00"); // exit: buyback paid
    expect(html).toContain("$57.48"); // net, coloured by its sign
    expect(html).toContain("charged as a cost in this module, and subtracted");
    expect(html).toContain("roll:expiry");
  });

  it("values every week at its own close, the last row being the header", () => {
    const html = text(<TrackerView t={T} />);
    for (const w of ["2026-W35", "2026-W36", "2026-W37", "+$32.13", "+$67.48"]) expect(html).toContain(w);
  });

  it("says so when a week had no short open, rather than showing a quiet week", () => {
    const html = text(<TrackerView t={{ ...T, integrity: { ...T.integrity, weeksWithoutAShort: ["2026-W37"] } }} />);
    expect(html).toContain("weeks with no short open at the close: 2026-W37");
  });

  it("renders an unpriceable position as dashes, never as zeros", () => {
    const blank = { ...T, header: { ...T.header, net: null, gross: null, returnOnLongCost: null } };
    const html = text(<TrackerView t={blank} />);
    expect(html).not.toContain("+$0.00");
    expect(html).toContain("—");
  });
});

describe("the arms, week by week", () => {
  const rows: PmccWeeklyRow[] = [
    { arm: "control", symbol: "XSP", week: "2026-W41", weekEnd: "2026-10-09", change: 120.5, cumulative: 120.5, positions: 1, unpriced: 0 },
    { arm: "shield", symbol: "XSP", week: "2026-W41", weekEnd: "2026-10-09", change: 40, cumulative: 40, positions: 1, unpriced: 0 },
    { arm: "shield", symbol: "XSP", week: "2026-W42", weekEnd: "2026-10-16", change: -15, cumulative: 25, positions: 1, unpriced: 1 },
  ];

  it("lays the arms side by side per week, and flags an unpriced week instead of guessing it", () => {
    const html = text(<WeeklyByArm rows={rows} />);
    expect(html).toContain("control · XSP");
    expect(html).toContain("shield · XSP");
    expect(html).toContain("2026-W42");
    expect(html).toContain("unpriced");
  });
});
