import { describe, it, expect } from "vitest";
import { sumDollarsAtRisk } from "../src/readers/desk.js";

/**
 * The Overview's "at risk" column in dollars for the four modules whose ledgers store the amount
 * per share (calendars' entry debit, pmcc's net debit, curve's and bwb's max loss). Until
 * 2026-09-24 it summed them raw, so those rows read ~100x too small beside meic's, flies' and
 * earnings' true dollars under the same header.
 */
describe("sumDollarsAtRisk", () => {
  const debit = (p: { d: number | null }) => p.d;

  it("is per-share x 100 x quantity, summed", () => {
    expect(sumDollarsAtRisk([{ d: 1.85, quantity: 2 }, { d: 0.6, quantity: 1 }], debit)).toBeCloseTo(430, 6);
  });

  it("reads a missing quantity as one contract, the ledgers' own rule", () => {
    expect(sumDollarsAtRisk([{ d: 2.1, quantity: null }], debit)).toBeCloseTo(210, 6);
  });

  it("skips an unrecorded amount, and is null when nothing is recorded -- never a zero", () => {
    expect(sumDollarsAtRisk([{ d: null, quantity: 3 }, { d: 1, quantity: 1 }], debit)).toBeCloseTo(100, 6);
    expect(sumDollarsAtRisk([{ d: null, quantity: 3 }], debit)).toBeNull();
    expect(sumDollarsAtRisk([], debit)).toBeNull();
  });
});
