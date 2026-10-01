import { describe, it, expect } from "vitest";
import { chipState, formatChange, formatPct, formatPrice } from "../src/components/shell/FuturesTicker";
import type { QuoteState } from "../src/lib/wsClient";

/**
 * The futures chips' colour and change. The change is the feed's own price against the feed's own
 * prior settle: a cached price (DXLink down) never gets a change or a direction colour, because the
 * two numbers would come from different sources and the colour would claim a move nobody saw.
 */

function q(over: Partial<QuoteState>): QuoteState {
  return { source: "dxlink", direction: null, ts: 1, ...over };
}

describe("futures chips", () => {
  it("a live price above the prior settle is green, with the change in points and percent", () => {
    const s = chipState(q({ last: 7762.25, prevClose: 7750 }));
    expect(s.tone).toBe("futures-chip-up");
    expect(s.change).toBeCloseTo(12.25);
    expect(s.changePct).toBeCloseTo(0.158, 3);
  });

  it("a live price below the prior settle is red", () => {
    expect(chipState(q({ last: 90.1, prevClose: 90.71 })).tone).toBe("futures-chip-down");
  });

  it("before any trade the chip prices off the mid", () => {
    expect(chipState(q({ bid: 4214.2, ask: 4214.4, prevClose: 4200 })).price).toBeCloseTo(4214.3);
  });

  it("no settle yet: a price, but no change and no colour", () => {
    const s = chipState(q({ last: 7762.25 }));
    expect(s.price).toBe(7762.25);
    expect(s.change).toBeUndefined();
    expect(s.tone).toBe("");
  });

  it("a cached price is muted and claims no change, even with a settle on hand", () => {
    const s = chipState(q({ source: "cache", last: 7762.25, prevClose: 7750 }));
    expect(s.change).toBeUndefined();
    expect(s.tone).toBe("futures-chip-cached");
  });

  it("bonds print in 32nds, prices and changes alike", () => {
    expect(formatPrice("ZB", 102.71875)).toBe("102'23");
    expect(formatPrice("ZB", 102.99)).toBe("103'00");
    expect(formatChange("ZB", -0.25)).toBe("−0'08");
    expect(formatChange("ES", 12.25)).toBe("+12.25");
    expect(formatPct(-0.0612)).toBe("−0.06%");
  });
});
