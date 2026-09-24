import { describe, it, expect } from "vitest";
import { bookFloor, positionPnl, type FlyPosition } from "../src/analytics/fliesPayoff.js";

/**
 * The shape of 2026-09-24's live control book: three completed flies and the 14:05 call spread
 * that never completed. Its worst case is reached twice -- at the 7695 point, where the spread is
 * fully lost and none of the flies pays, and again at 7710. `worstAt` can only name one;
 * `worstPlaces` names both, so the caption cannot hide the low under the settlement. It also pins
 * the merge: the scan probes a cent either side of each strike, and the 7695 low arrives as two
 * runs (7694.99, 7695.01) that must read as one place.
 */
const fly = (side: string, center: number): FlyPosition =>
  ({ kind: "fly", side, center, wingWidth: 5, farWidth: null, net: 0.25, quantity: 1, fees: 6.89, status: "open" }) as FlyPosition;

describe("bookFloor's worst places", () => {
  it("finds both lows on the 2026-09-24 live book", () => {
    const book: FlyPosition[] = [
      fly("put", 7690),
      fly("put", 7705),
      fly("call", 7700),
      { kind: "short_vertical", side: "call", center: 7685, wingWidth: 5, farWidth: null, net: 2.55, quantity: 1, fees: 3.44, status: "open" } as FlyPosition,
    ];
    const f = bookFloor(book);
    const places = f.worstPlaces.map((w) => [Math.round(w.lo), Math.round(w.hi), w.tail]);
    expect(places).toEqual([
      [7695, 7695, null],
      [7710, 7710, null],
    ]);
  });
});

describe("a settled row read at a hypothetical price", () => {
  // The 14:05 call spread: settled at 7704.13 with $10 of expiry fees folded into its $13.44.
  const settled = {
    kind: "short_vertical", side: "call", center: 7685, wingWidth: 5, farWidth: null, net: 2.55, quantity: 1,
    fees: 13.44, status: "settled", settlementPrice: 7704.13,
  } as FlyPosition;

  it("reproduces its recorded P&L at its own settlement price", () => {
    expect(positionPnl(settled, 7704.13)).toBeCloseTo(-258.44, 2);
  });

  it("carries the fee the priced point would charge, not the one its settlement did", () => {
    // Below 7685 nothing is in the money: no expiry fee, so only the $3.44 commission comes off.
    expect(positionPnl(settled, 7680)).toBeCloseTo(255 - 3.44, 2);
    // Without the settlement price the folded fee stays, as before.
    expect(positionPnl({ ...settled, settlementPrice: null }, 7680)).toBeCloseTo(255 - 13.44, 2);
  });
});

