import { describe, it, expect } from "vitest";
import { computeGexProfile, dollarGamma, strikeGamma } from "../src/analytics/gex.js";

/**
 * One gamma per strike (2026-10-09), the same cases as core's tests/test_gex_one_gamma.py so the page
 * and the recorder cannot drift apart: a strike's call and put carry the out-of-the-money side's
 * gamma, so a balanced at-the-money strike takes its sign from open interest, not quote noise.
 */

const SPOT = 7803;

function chain(strike: number, callGamma: number, putGamma: number, callOi: number, putOi: number) {
  return {
    entries: [
      { strikePrice: strike, streamerSymbol: "C", optionType: "C", sharesPerContract: null },
      { strikePrice: strike, streamerSymbol: "P", optionType: "P", sharesPerContract: null },
    ],
    greeks: new Map([
      ["C", { gamma: callGamma, iv: 10.78 }],
      ["P", { gamma: putGamma, iv: 10.76 }],
    ]),
    oi: new Map([
      ["C", callOi],
      ["P", putOi],
    ]),
  };
}

describe("one gamma per strike", () => {
  it("takes the out-of-the-money side's gamma, the call at spot", () => {
    expect(strikeGamma(105, 100, 0.02, 0.03)).toBe(0.02);
    expect(strikeGamma(95, 100, 0.02, 0.03)).toBe(0.03);
    expect(strikeGamma(100, 100, 0.02, 0.03)).toBe(0.02);
  });

  it("falls back to the other side when one is missing", () => {
    expect(strikeGamma(105, 100, 0, 0.03)).toBe(0.03);
    expect(strikeGamma(95, 100, 0.02, 0)).toBe(0.02);
    expect(strikeGamma(95, 100, 0, 0)).toBe(0);
  });

  it("signs a balanced at-the-money strike by open interest, not by the two quotes' vols", () => {
    const c = chain(7790, 0.0097, 0.0105, 2425, 2270);
    expect(0.0097 * 2425 - 0.0105 * 2270).toBeLessThan(0); // the old arithmetic's sign
    const out = computeGexProfile(c.entries, c.greeks, c.oi, new Map(), SPOT);
    if (!out.ok) throw new Error(out.error);
    expect(out.series[0].net_gex).toBeCloseTo(dollarGamma(0.0105, 2425 - 2270, 100, SPOT), -1);
  });

  it("uses the same one gamma for the volume series", () => {
    const c = chain(7810, 0.009, 0.012, 100, 100);
    const vol = new Map([
      ["C", 500],
      ["P", 200],
    ]);
    const out = computeGexProfile(c.entries, c.greeks, c.oi, vol, SPOT);
    if (!out.ok) throw new Error(out.error);
    expect(out.series[0].net_gex).toBe(0);
    expect(out.series[0].net_gex_vol).toBeCloseTo(dollarGamma(0.009, 300, 100, SPOT), -1);
  });
});
