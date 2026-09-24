import { describe, it, expect } from "vitest";
import { bookFloor, type FlyPosition } from "../src/analytics/fliesPayoff.js";

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
