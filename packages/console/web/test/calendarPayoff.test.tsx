import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { BuilderPayoff } from "@console/shared";
import { exampleWords, legOrder, legOrderWords, markers, modeledWords, TradeShapeView } from "../src/pages/Builder/TradeShape";

/** A put calendar at the money as the module returns it: L1 sold on the near expiry, L2 bought on the far. */
const CAL: BuilderPayoff = {
  name: "cal",
  symbol: "SPX",
  state: "placed",
  sketch: {
    legs: [
      { name: "L1", right: "P", side: -1, at: 0, label: "0", exact: true },
      { name: "L2", right: "P", side: 1, at: 0, label: "0", exact: true, expiry: "far" },
    ],
    curve: [
      [-10, -1.2],
      [0, 2.8],
      [10, -1.2],
    ],
    ticks: [{ at: 0, label: "0 · spot" }],
    exact: true,
    top: "peak, modeled",
    bottom: "max loss",
    modeled: true,
  },
  source: { kind: "live", at: "2026-11-19T10:00" },
  spot: 6000,
  atm: 6000,
  expiry: "2026-12-18",
  far_expiry: "2027-01-15",
  legs: [
    { name: "L1", right: "P", side: -1, strike: 6000, offset: 0 },
    { name: "L2", right: "P", side: 1, strike: 6000, offset: 0, expiry: "far" },
  ],
  prices: { mid: -24.5 },
  max_profit: null,
  max_loss: 2450,
  modeled: { peak: 3110.4, legs: [{ name: "L2", iv: 0.184, days: 28 }] },
  curve: [],
  breakevens: [],
  chain: [{ strike: 6000, offset: 0, put: { bid: 1, ask: 1.1, delta: -0.5 }, call: { bid: 1, ask: 1.1, delta: 0.5 }, gap: false }],
};

describe("a calendar's payoff", () => {
  it("names the far leg on the chart and on the chain", () => {
    expect(markers(CAL.sketch!)[0]!.text).toBe("L1 · Short Put · L2 · Long Put · far");
    expect(legOrder(CAL.legs![1]!)).toBe("BTO · L2 · Long Put · far");
    expect(legOrderWords(CAL.legs![1]!)).toBe("Buy to open · L2 · Long Put · on the far expiry");
    expect(legOrder(CAL.legs![0]!)).toBe("STO · L1 · Short Put");
  });

  it("says the peak is the model's, with the vol it held the far leg at", () => {
    expect(modeledWords(CAL.modeled!)).toBe("L2 at 18.4% vol, 28 days left");
    expect(modeledWords({ peak: 0, legs: [{ name: "L2", iv: null, days: 28 }] })).toBe("L2 at its intrinsic value, 28 days left");
    expect(exampleWords(CAL)).toBe(
      "For example, on live quotes, 2026-11-19 at 10:00: debit 24.50 at the mid, about +$3110.40 at most at the near expiry (modeled: L2 at 18.4% vol, 28 days left), at worst -$2450.00 a contract.",
    );
  });

  it("titles and captions the shape as modeled at the near expiry", () => {
    const out = renderToString(<TradeShapeView p={CAL} />);
    expect(out).toContain("the shape · at the near expiry, modeled");
    expect(out).toContain("peak, modeled");
    expect(out).toContain("the peak is the model&#x27;s, never a bound");
    expect(out).not.toContain("one structure at expiry");
  });
});
