import { describe, it, expect } from "vitest";
import { heroOf, shapeOf, worstNet, type SummaryTrade } from "../src/readers/summary/common.js";

/**
 * The cards' second faces read the per-trade shape: median, average win and loss, the worst trade.
 * A side with no trade is n/r (null), never zero -- a window with no loss has no payoff ratio,
 * and a zero average loss would draw one as infinite.
 */
describe("shapeOf", () => {
  it("takes the median of an even count as the mean of the middle two", () => {
    expect(shapeOf([-30, 10, 20, 40]).median).toBe(15);
    expect(shapeOf([5, -1, 3]).median).toBe(3);
  });

  it("averages wins and losses apart, and names the worst trade", () => {
    const s = shapeOf([100, 50, -30, -90, 0]);
    expect(s.avgWin).toBe(75);
    expect(s.avgLoss).toBe(-60);
    expect(s.worst).toBe(-90);
  });

  it("leaves a side with no trade null rather than zero", () => {
    expect(shapeOf([10, 20]).avgLoss).toBeNull();
    expect(shapeOf([-10]).avgWin).toBeNull();
    expect(shapeOf([])).toEqual({ median: null, avgWin: null, avgLoss: null, worst: null });
  });

  it("rides on the hero over the same trades the totals sum", () => {
    const trade = (net: number): SummaryTrade => ({ session: "2026-10-23", arm: "a", gross: net, fees: 0, settlement: 0, slippage: null, net });
    const hero = heroOf([trade(40), trade(-10), trade(25)], false, 0);
    expect(hero.shape).toEqual({ median: 25, avgWin: 32.5, avgLoss: -10, worst: -10 });
    expect(worstNet([trade(40), trade(-10)])).toBe(-10);
  });
});
