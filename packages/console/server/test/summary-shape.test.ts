import { describe, it, expect } from "vitest";
import { heldTogether, heroOf, overlapOf, rOf, shapeOf, t95, wilson95, worstNet, type SummaryTrade } from "../src/readers/summary/common.js";

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
    expect(shapeOf([])).toMatchObject({ median: null, avgWin: null, avgLoss: null, worst: null, n: 0, mean: null, ci95: null });
  });

  it("rides on the hero over the same trades the totals sum", () => {
    const trade = (net: number): SummaryTrade => ({ session: "2026-10-23", arm: "a", gross: net, fees: 0, settlement: 0, slippage: null, net });
    const hero = heroOf([trade(40), trade(-10), trade(25)], false, 0);
    expect(hero.shape).toMatchObject({ median: 25, avgWin: 32.5, avgLoss: -10, worst: -10, n: 3, mean: 18.33 });
    expect(worstNet([trade(40), trade(-10)])).toBe(-10);
  });
});

/**
 * How sure the figures are (B2): net per trade with its 95% interval and t, the win rate's Wilson
 * interval, and a skew flag in both directions. Values worked by hand.
 */
describe("shapeOf, uncertainty", () => {
  it("gives net per trade its Student's t interval and t", () => {
    // mean 3, sd √2.5 ≈ 1.581, se ≈ 0.707, t(4) = 2.776 -> ±1.963
    const s = shapeOf([1, 2, 3, 4, 5]);
    expect(s).toMatchObject({ n: 5, mean: 3, sd: 1.58, ci95: [1.04, 4.96], t: 4.24, skew: null });
  });

  it("uses 1.96 from 30 trades on", () => {
    expect(t95(4)).toBe(2.776);
    expect(t95(29)).toBe(2.045);
    expect(t95(30)).toBe(1.96);
  });

  it("gives the win rate a Wilson interval that never leaves 0..1", () => {
    expect(wilson95(5, 5)![0]).toBeCloseTo(0.5655, 3);
    expect(wilson95(5, 5)![1]).toBe(1);
    expect(wilson95(0, 10)![0]).toBe(0);
    expect(shapeOf([1, 2, 3, 4, 5]).winRateCi95![0]).toBeCloseTo(0.5655, 3);
    expect(wilson95(0, 0)).toBeNull();
  });

  it("flags premium selling's shape -- rare large losses, median above mean -- as left skew", () => {
    const s = shapeOf([10, 10, 10, 10, 10, 10, 10, 10, 10, -100]);
    expect(s.median).toBe(10);
    expect(s.mean).toBe(-1);
    expect(s.skew).toBe("left");
    expect(shapeOf([-10, -10, -10, -10, -10, -10, -10, -10, -10, 100]).skew).toBe("right");
  });

  it("says nothing it cannot: one trade has no spread, and no spread has no interval", () => {
    expect(shapeOf([7])).toMatchObject({ n: 1, mean: 7, sd: null, ci95: null, t: null, skew: null });
    expect(shapeOf([5, 5, 5])).toMatchObject({ sd: 0, ci95: null, t: null, skewness: null });
  });
});

/** Results in R (B3): net over the trade's max loss, over the trades whose risk was recorded. */
describe("rOf", () => {
  const trade = (net: number): SummaryTrade => ({ session: "2026-10-23", arm: "a", gross: net, fees: 0, settlement: 0, slippage: null, net });
  it("averages net over risk, skipping unrecorded or zero risk", () => {
    const ts = [trade(100), trade(-200), trade(50), trade(10)];
    const risk = new Map([[ts[0], 400], [ts[1], 400], [ts[2], 0]]);
    const r = rOf(ts, (t) => risk.get(t) ?? null);
    expect(r.rN).toBe(2);
    expect(r.meanR).toBe(-0.125);
  });

  it("rides on the hero only when the module has a risk", () => {
    expect(heroOf([trade(10)], false, 0).shape?.meanR).toBeUndefined();
    expect(heroOf([trade(10)], false, 0, () => 100).shape).toMatchObject({ meanR: 0.1, rN: 1, rCi95: null });
  });
});

/** B2's overlap flag: sessions that closed several trades make the interval read narrower than it is. */
describe("overlapOf", () => {
  const at = (session: string): SummaryTrade => ({ session, arm: "a", gross: 1, fees: 0, settlement: 0, slippage: null, net: 1 });
  it("counts the sessions with more than one trade, or nothing when none had", () => {
    expect(overlapOf([at("d1"), at("d2")])).toBeNull();
    expect(overlapOf([at("d1"), at("d1"), at("d1"), at("d2")])).toEqual({ sessions: 1, of: 2, maxPerSession: 3, heldTogether: null });
    expect(heroOf([at("d1"), at("d1")], false, 0).shape?.overlap).toEqual({ sessions: 1, of: 1, maxPerSession: 2, heldTogether: null });
  });
});

describe("heldTogether (B2, multi-day)", () => {
  const span = (arm: string, entry: string, session: string): SummaryTrade => ({ session, entry, arm, gross: 1, fees: 0, settlement: 0, slippage: null, net: 1 });
  it("counts trades held alongside another in the same arm, never across arms", () => {
    const ladder = [span("a", "2026-10-01", "2026-10-08"), span("a", "2026-10-05", "2026-10-15"), span("a", "2026-10-20", "2026-10-27")];
    expect(heldTogether(ladder)).toBe(2);
    expect(heldTogether([span("a", "2026-10-01", "2026-10-08"), span("b", "2026-10-02", "2026-10-09")])).toBe(0);
    expect(overlapOf(ladder)).toMatchObject({ sessions: 0, heldTogether: 2 });
  });
  it("is null, not zero, when no trade carries an entry", () => {
    const at = (session: string): SummaryTrade => ({ session, arm: "a", gross: 1, fees: 0, settlement: 0, slippage: null, net: 1 });
    expect(heldTogether([at("d1")])).toBeNull();
  });
});
