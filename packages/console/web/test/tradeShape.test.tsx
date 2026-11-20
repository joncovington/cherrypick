import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { BuilderPayoff, BuilderSketch } from "@console/shared";
import { exampleWords, labelRows, legOrder, legOrderWords, markers, offsetWords, sendable, shapeGeometry, TradeShapeView, valueAt } from "../src/pages/Builder/TradeShape";
import { closeAbbr, closeVerb, legLabel, legSentence, legWords, nextLegId, openAbbr, openVerb, parts, signedQty, titleCase, writtenLeg } from "../src/pages/Builder/legWords";

const q = (delta: number) => ({ bid: 1, ask: 1.1, delta });

/** The module's sketch of iron condor 1345: put spread 5 and 10 below the spot, call spread 5 and 10 above. */
const SKETCH: BuilderSketch = {
  legs: [
    { name: "short_put", right: "P", side: -1, at: -5, label: "−5", exact: true },
    { name: "long_put", right: "P", side: 1, at: -10, label: "−10", exact: true },
    { name: "short_call", right: "C", side: -1, at: 5, label: "+5", exact: true },
    { name: "long_call", right: "C", side: 1, at: 10, label: "+10", exact: true },
  ],
  curve: [
    [-20, -3.5],
    [-10, -3.5],
    [-5, 1.5],
    [5, 1.5],
    [10, -3.5],
    [20, -3.5],
  ],
  ticks: [
    { at: -10, label: "−10" },
    { at: -5, label: "−5" },
    { at: 0, label: "0 · spot" },
    { at: 5, label: "+5" },
    { at: 10, label: "+10" },
  ],
  exact: true,
  top: "max profit",
  bottom: "max loss",
};

const PLACED: BuilderPayoff = {
  name: "iron-condor-1345",
  symbol: "SPX",
  state: "placed",
  sketch: SKETCH,
  source: { kind: "recorded", at: "2026-11-17T13:45", source: "recorded-0dte" },
  spot: 7332.5,
  atm: 7330,
  expiry: "2026-11-17",
  legs: [
    { name: "short_put", right: "P", side: -1, strike: 7325, offset: -5 },
    { name: "long_put", right: "P", side: 1, strike: 7320, offset: -10 },
    { name: "short_call", right: "C", side: -1, strike: 7335, offset: 5 },
    { name: "long_call", right: "C", side: 1, strike: 7340, offset: 10 },
  ],
  prices: { mid: 3.95 },
  max_profit: 395,
  max_loss: 105,
  chain: [
    { strike: 7320, offset: -10, put: q(-0.35), call: q(0.64), gap: false },
    { strike: 7325, offset: -5, put: q(-0.42), call: q(0.58), gap: false },
    { strike: 7330, offset: 0, put: q(-0.45), call: q(0.53), gap: false },
    { strike: 7335, offset: 5, put: q(-0.53), call: q(0.47), gap: false },
    { strike: 7340, offset: 10, put: q(-0.59), call: q(0.43), gap: true },
  ],
};

const html = (p: BuilderPayoff) => renderToString(<TradeShapeView p={p} />).replace(/<!--\s*-->/g, "");

describe("a leg's two spellings", () => {
  it("reads the page's spelling and the older one alike", () => {
    expect(parts({ action: "STO", qty: 2, side: "put" })).toEqual({ sign: -1, n: 2, right: "P" });
    expect(parts({ side: -2, right: "P" })).toEqual({ sign: -1, n: 2, right: "P" });
    expect(parts({ action: "BTO", side: "call" })).toEqual({ sign: 1, n: 1, right: "C" });
    expect(legWords({ action: "STO", qty: 2, side: "put" })).toBe(legWords({ side: -2, right: "P" }));
  });

  it("writes an older leg in the page's spelling, keeping its name and strike", () => {
    const strike = { rule: "from_leg", leg: "L1", points: -5 };
    expect(writtenLeg({ name: "L2", right: "P", side: -2, strike })).toEqual({ name: "L2", action: "STO", qty: 2, side: "put", strike });
    const w = { name: "L1", action: "BTO", qty: 1, side: "call", strike };
    expect(writtenLeg(w)).toEqual(w);
    expect(signedQty({ action: "STO", qty: 3, side: "call" })).toBe(-3);
  });
});

describe("a leg in words", () => {
  it("names the order that opens a leg and the one that closes it", () => {
    expect(openVerb(-2)).toBe("Sell to open");
    expect(openVerb(1)).toBe("Buy to open");
    expect(closeVerb(-1)).toBe("Buy to close");
    expect(closeVerb(3)).toBe("Sell to close");
    expect([openAbbr(-1), openAbbr(1), closeAbbr(-1), closeAbbr(1)]).toEqual(["STO", "BTO", "BTC", "STC"]);
  });

  it("names a leg by its ID and its side, right and contracts", () => {
    expect(legWords({ side: -2, right: "P" })).toBe("Short Put ×2");
    expect(legLabel({ name: "L2", side: -2, right: "P" })).toBe("L2 · Short Put ×2");
    expect(legLabel({ name: "short_put", side: -1, right: "P" })).toBe("Short Put");
    expect(titleCase("long_call")).toBe("Long Call");
  });

  it("gives the next ID past the highest held and past any handed out, so a removed one is not reused", () => {
    expect(nextLegId([])).toBe("L1");
    expect(nextLegId([{ name: "L1" }, { name: "L3" }])).toBe("L4");
    expect(nextLegId([{ name: "L1" }], 3)).toBe("L4");
    expect(nextLegId([{ name: "short_put" }, { name: "long_put" }])).toBe("L3");
  });

  it("says where each leg goes, naming the leg it is placed from", () => {
    const l1 = { name: "L1", side: 1, right: "P", strike: { rule: "spot", points: 0 } };
    const l2 = { name: "L2", side: -2, right: "P", strike: { rule: "from_leg", leg: "L1", points: -5 } };
    const by = { L1: l1, L2: l2 };
    expect(legSentence(l1, by)).toBe("Buy to open a L1 · Long Put at the strike nearest the spot");
    expect(legSentence(l2, by)).toBe("Sell to open 2 × L2 · Short Put 5 below the L1 · Long Put");
    expect(legSentence({ name: "short_call", side: -1, right: "C", strike: { rule: "same_as", leg: "short_put" } }, { short_put: { name: "short_put", side: -1, right: "P" } })).toBe(
      "Sell to open a Short Call at the same strike as the Short Put",
    );
  });
});

describe("the trade shape", () => {
  it("names a chain row by its distance from the strike nearest the spot", () => {
    expect(offsetWords(0)).toBe("0 · spot");
    expect(offsetWords(-10)).toBe("−10");
    expect(offsetWords(7.5)).toBe("+7.5");
  });

  it("names an order with its contracts when more than one", () => {
    expect(legOrder({ name: "L2", side: -2, right: "P" })).toBe("STO 2 · L2 · Short Put");
    expect(legOrderWords({ name: "L2", side: -2, right: "P" })).toBe("Sell to open 2 · L2 · Short Put");
    expect(legOrder({ name: "short_call", side: -1, right: "C" })).toBe("STO · Short Call");
  });

  it("puts legs on one position into one marker, and marks one holding more than a contract", () => {
    const fly: BuilderSketch = {
      ...SKETCH,
      legs: [
        { name: "short_put", right: "P", side: -1, at: 0, label: "0", exact: true },
        { name: "short_call", right: "C", side: -1, at: 0, label: "0", exact: true },
        { name: "L5", right: "P", side: -2, at: -5, label: "−5", exact: true },
      ],
    };
    const m = markers(fly);
    expect(m.map((x) => x.text)).toEqual(["L5 · Short Put ×2", "Short Put · Short Call"]);
    expect(m[0]!.many).toBe(true);
  });

  it("puts a crowded tick's label on the second row", () => {
    expect(labelRows([10, 20, 100, 110, 200])).toEqual([0, 1, 0, 1, 0]);
  });

  it("draws the module's curve without reworking it", () => {
    const g = shapeGeometry(SKETCH.curve);
    expect(g.top).toBe(1.5);
    expect(g.bottom).toBe(-3.5);
    expect(valueAt(SKETCH.curve, -7.5)).toBe(-1);
    expect(g.line.split(" L").length).toBe(SKETCH.curve.length);
  });

  it("says the example is an example, with where it came from and its money", () => {
    expect(exampleWords(PLACED)).toBe("For example, on the last recorded session, 2026-11-17 at 13:45: credit 3.95 at the mid, at most +$395.00, at worst -$105.00 a contract.");
    expect(exampleWords({ ...PLACED, source: { kind: "live", at: "2026-11-18T10:02" }, prices: { mid: -1.2 } })).toContain("on live quotes, 2026-11-18 at 10:02: debit 1.20");
  });

  it("sends an edit without the keys the module never reads from one", () => {
    expect(sendable({ name: "x", register: {}, enabled: true, symbol: "SPX", legs: [] })).toEqual({ symbol: "SPX", legs: [] });
  });

  it("shows the generic shape beside the chain: words, not dollars; distances, not strikes; no bid or ask", () => {
    const out = html(PLACED);
    expect(out).toContain("the shape · one structure at expiry");
    expect(out).toContain("max profit");
    expect(out).toContain("max loss");
    expect(out).toContain("0 · spot");
    expect(out).toContain("STO · Short Put");
    expect(out).toContain('title="Sell to open · Short Put"');
    expect(out).toContain("BTO · Long Call");
    expect(out).toContain("For example, on the last recorded session");
    expect(out).not.toContain("7325");
    expect(out).not.toContain("7332");
    expect(out).not.toMatch(/>bid</);
    expect(out).toContain("tchain-gap");
  });

  it("still shows the shape when no session can place the legs, and says why there is no example", () => {
    const out = html({ name: "g", symbol: "GLD", state: "not_placed", sketch: SKETCH, live: "no_expiry", recorded: "a later expiry is placed on live quotes only" });
    expect(out).toContain("the shape · one structure at expiry");
    expect(out).toContain("No example yet. On live quotes: no_expiry. On the last recorded session: a later expiry is placed on live quotes only.");
    expect(out).not.toContain("tchain-table");
  });
});
