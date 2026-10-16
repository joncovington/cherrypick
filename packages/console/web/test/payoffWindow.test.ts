import { describe, it, expect } from "vitest";
import { extendFlat, visibleYRange } from "../src/components/chart/payoffWindow";

// An iron condor's expiry payoff: flat loss outside the wings, flat credit between the shorts.
const xs = [7690, 7700, 7750, 7800, 7810];
const ys = [-800, -800, 200, -800, -800];

describe("windowing a payoff curve (both profit forests)", () => {
  it("extends a curve flat to a wider window, and leaves a narrower one alone", () => {
    expect(extendFlat(xs, ys, 7600, 7900)).toEqual({ xs: [7600, ...xs, 7900], ys: [-800, ...ys, -800] });
    expect(extendFlat(xs, ys, 7700, 7800)).toEqual({ xs, ys });
  });

  it("fits y to what is visible inside the window", () => {
    expect(visibleYRange(xs, ys, 7740, 7760)).toEqual({ min: 200, max: 200 });
    expect(visibleYRange(xs, ys, 7600, 7900)).toEqual({ min: -800, max: 200 });
  });

  it("a window wholly outside the curve reads its flat edge value", () => {
    expect(visibleYRange(xs, ys, 7500, 7600)).toEqual({ min: -800, max: -800 });
  });
});
