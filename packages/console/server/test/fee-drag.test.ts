import { describe, expect, it } from "vitest";
import { feeDragPct, sumIfAllRecorded, tradingFees } from "@console/shared";

/**
 * The one fee-drag definition (root CLAUDE.md): trading fees -- never settlement -- over the premium
 * collected. Before it was shared, flies divided the fee total by gross P&L and MEIC divided fees
 * with settlement folded in by the credit, while the costs page had it right; three surfaces, three
 * numbers under one name.
 */

describe("tradingFees", () => {
  it("takes settlement out of a fee total that folds it in", () => {
    expect(tradingFees(40, 10)).toBe(30);
    expect(tradingFees(6.89, 0)).toBe(6.89);
  });

  it("keeps the total where the split was never recorded", () => {
    expect(tradingFees(9.5, null)).toBe(9.5);
  });

  it("knows nothing of a fee total that was never recorded", () => {
    expect(tradingFees(null, 10)).toBeNull();
  });
});

describe("feeDragPct", () => {
  it("is trading fees over premium, as a percentage", () => {
    expect(feeDragPct(30, 240)).toBe(12.5);
  });

  it("is null, never 0 or Infinity, when nothing was collected or a side is unknown", () => {
    expect(feeDragPct(5, 0)).toBeNull();
    expect(feeDragPct(5, -40)).toBeNull();
    expect(feeDragPct(5, null)).toBeNull();
    expect(feeDragPct(null, 240)).toBeNull();
    expect(feeDragPct(Number.NaN, 240)).toBeNull();
  });

  it("still reads a scope with premium and no fees as 0%", () => {
    expect(feeDragPct(0, 240)).toBe(0);
  });
});

describe("sumIfAllRecorded", () => {
  it("is the sum when every row recorded the cost", () => {
    expect(sumIfAllRecorded(10, 3, 3)).toBe(10);
    expect(sumIfAllRecorded(null, 0, 0)).toBe(0);
  });

  it("is null (n/r) when any contributing row never recorded it", () => {
    expect(sumIfAllRecorded(10, 2, 3)).toBeNull();
    expect(sumIfAllRecorded(0, 0, 101)).toBeNull();
  });
});
