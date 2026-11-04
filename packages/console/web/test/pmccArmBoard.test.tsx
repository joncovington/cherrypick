import { describe, expect, it } from "vitest";
import type { PmccOpenPosition, PmccTracker } from "@console/shared";
import { armColumns, pmccSteps, pnlPoints } from "../src/pages/Pmcc/PmccArmBoard";

/** PMCC by arm (2026-11-04): a column per arm, read per symbol, never pooled. */

function pos(symbol: string, arm: string, net: number | null, exposedTicks = 0): PmccOpenPosition {
  return { positionId: `${symbol}:${arm}`, symbol, arm, unrealisedNet: net, exposedTicks, markedTicks: 10 } as unknown as PmccOpenPosition;
}

function tracker(over: Partial<PmccTracker> = {}): PmccTracker {
  return {
    position: { positionId: "QQQ:shield", symbol: "QQQ", arm: "shield", era: "shield", status: "open", exitReason: null, entrySession: "2026-10-06", closedSession: null, quantity: 1, entrySpot: 600, rollCount: 2, exposureTicks: 0, lifecycle: "held_long" },
    header: {} as PmccTracker["header"],
    longLots: [{ opened: "2026-10-06", expiration: "2027-09-17", spotOpen: 600, strike: 480, quantity: 1, cost: -12500, priceNow: 130, valueNow: 13000, gain: 500, extrinsicPaid: 5, extrinsicNow: 4, deltaNow: 0.9, status: "open" }],
    currentShort: null,
    shorts: [
      { n: 1, legRole: "short", status: "closed", opened: "2026-10-06", closed: "2026-10-10", daysHeld: 4, spotOpen: 600, spotClose: 605, strike: 610, expiration: "2026-10-10", sold: 4.8, bought: 0.1, entry: 480, exit: -10, how: "expired", why: null, gross: 470, fees: 2, slippage: 1, net: 467, extrinsicSold: 4.8, extrinsicLeft: 0, extrinsicCaptured: 4.8 },
      { n: 2, legRole: "short", status: "closed", opened: "2026-10-13", closed: "2026-10-15", daysHeld: 2, spotOpen: 605, spotClose: 618, strike: 615, expiration: "2026-10-17", sold: 4.2, bought: 6.5, entry: 420, exit: -650, how: "rolled", why: "breach", gross: -230, fees: 2, slippage: 1, net: -233, extrinsicSold: 4.2, extrinsicLeft: 1, extrinsicCaptured: 3.2 },
      { n: 3, legRole: "short", status: "open", opened: "2026-10-15", closed: null, daysHeld: null, spotOpen: 618, spotClose: null, strike: 625, expiration: "2026-10-24", sold: 5.1, bought: null, entry: 510, exit: null, how: null, why: null, gross: null, fees: 2, slippage: 1, net: null, extrinsicSold: 5.1, extrinsicLeft: null, extrinsicCaptured: null },
    ],
    weeks: [
      { week: "2026-W41", weekEnd: "2026-10-10", priced: true, spot: 605, longMark: 0, shortOpen: 0, shortRealised: 0, costs: 0, net: 210, change: 210, returnOnLongCost: null, returnOnNotional: null, netDelta: null },
      { week: "2026-W42", weekEnd: "2026-10-17", priced: false, spot: null, longMark: null, shortOpen: null, shortRealised: null, costs: null, net: null, change: null, returnOnLongCost: null, returnOnNotional: null, netDelta: null },
      { week: "2026-W43", weekEnd: "2026-10-24", priced: true, spot: 620, longMark: 0, shortOpen: 0, shortRealised: 0, costs: 0, net: 395, change: 185, returnOnLongCost: null, returnOnNotional: null, netDelta: null },
    ],
    integrity: { exposureTicks: 0, unpricedWeeks: 1, weeksWithoutAShort: [], costsBasis: null },
    asOf: 0,
    ...over,
  };
}

describe("the PMCC arm board", () => {
  it("groups open positions by arm, the module's arms first, symbols sorted, and counts the exposed shorts", () => {
    const cols = armColumns([pos("SLV", "shield_hold", 61, 40), pos("QQQ", "shield", 388), pos("GLD", "shield", -58), pos("QQQ", "shield_guard", -36), pos("QQQ", "shield_hold", 512, 12)]);
    expect(cols.map((c) => c.arm)).toEqual(["shield", "shield_hold", "shield_guard"]);
    expect(cols[0]!.positions.map((p) => p.symbol)).toEqual(["GLD", "QQQ"]);
    expect(cols[1]!.exposed).toBe(2);
    // never a pooled total: a column carries its positions, nothing summed
    expect(Object.keys(cols[0]!)).toEqual(["arm", "positions", "exposed"]);
  });

  it("draws P&L from zero at entry through each priced week's close; an unpriced week is left off, never zero", () => {
    expect(pnlPoints(tracker())).toEqual([
      { date: "2026-10-06", net: 0 },
      { date: "2026-10-10", net: 210 },
      { date: "2026-10-24", net: 395 },
    ]);
  });

  it("reads the chain off the tracker: the long bought, the first short sold, each later one a roll, and how each ended", () => {
    const steps = pmccSteps(tracker());
    expect(steps.map((s) => s.role)).toEqual(["open", "sell", "expired", "roll", "close", "roll"]);
    expect(steps[0]!.cash).toBe(-12500);
    expect(steps[0]!.what).toBe("buy 1 2027-09-17 480C");
    expect(steps[1]!.price).toBe("4.80 cr");
    expect(steps[4]!.what).toBe("rolled · breach · short net -$233.00");
    expect(steps[4]!.price).toBe("6.50 db");
  });
});
