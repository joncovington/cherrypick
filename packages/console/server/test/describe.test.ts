/**
 * Ported from scout's test_describe.py. These are not ordinary unit tests: each one replays an
 * observed reference-platform card, and together they are the *evidence* that justified the formulae
 * in describe.ts. If one breaks, the code no longer reproduces the observations it was fitted to —
 * which is a different and worse thing than a failing assertion.
 */
import { describe as suite, it, expect } from "vitest";
import type { Leg, Extremum } from "../src/analytics/payoff.js";
import {
  rawReturn,
  annualizedReturn,
  projectedYield12m,
  score,
  probableRisk2sd,
  probWorthless,
  bsGreeks,
  direction,
  comboSpreadPct,
  strategyExplanation,
  greeksExplanation,
  shortPutSuggestion,
  hasWeeklyCadence,
  checklist,
  checklistDirectional,
} from "../src/analytics/describe.js";

function leg(kind: "call" | "put" | "stock", quantity: number, price: number, strike?: number): Leg {
  return { kind, quantity, price, strike: strike ?? null };
}
const bounded = (value: number): Extremum => ({ value, unbounded: false });
const unbounded: Extremum = { value: null, unbounded: true };
const statuses = (items: Array<{ status: string }>): string[] => items.map((i) => i.status);

const SHORT_PUT = [leg("put", -1, 1.5, 95)];

suite("annualized return", () => {
  it("matches the reverse-engineered reference pairs, including a different DTE", () => {
    // A linear annualization reproduces none of these; the 46d rows are what rule it out.
    expect(rawReturn(150, 900)).toBeCloseTo(0.1667, 4);
    expect(annualizedReturn(150, 900, 25)!).toBeCloseTo(8.4934, 1);
    expect(rawReturn(113, 987)).toBeCloseTo(0.1145, 4);
    expect(annualizedReturn(113, 987, 25)!).toBeCloseTo(3.8675, 1);
    // HPE 2026-08-03 (live): $123.50 / $3,476.50 / 46 DTE -> displayed 31.91%.
    expect(annualizedReturn(123.5, 3476.5, 46)!).toBeCloseTo(0.3191, 2);
    // KWEB covered call: $73.50 / $2,789.50 / 46 DTE -> 22.93%.
    expect(annualizedReturn(73.5, 2789.5, 46)!).toBeCloseTo(0.2293, 2);
    // USO covered call: $230.00 / $11,850.00 / 46 DTE -> 16.48%.
    expect(annualizedReturn(230, 11850, 46)!).toBeCloseTo(0.1648, 2);
  });

  it("degrades on bad inputs rather than returning a wrong number", () => {
    expect(annualizedReturn(100, 0, 25)).toBeNull();
    expect(annualizedReturn(100, 500, 0)).toBeNull();
    expect(rawReturn(Number.NaN, 500)).toBeNull();
  });
});

suite("projected 12M yield", () => {
  it("matches the KWEB covered-call card — simple addition, not compounding", () => {
    expect(projectedYield12m(0.2293, 0.0736)!).toBeCloseTo(0.3029, 4);
    expect(projectedYield12m(null, 0.0736)).toBeNull();
    expect(projectedYield12m(0.2293, null)).toBeNull();
  });

  it("equals the annualized return when the dividend is zero (USO, a commodity ETF)", () => {
    expect(projectedYield12m(0.1648, 0)!).toBeCloseTo(0.1648, 6);
  });
});

suite("score", () => {
  it("matches the APD same-underlying vertical fit", () => {
    const putVertical = [leg("put", -1, 0, 290), leg("put", 1, 0, 280)];
    expect(score(0.5824, putVertical, bounded(310), bounded(-690))!).toBeCloseTo(84, 0);

    const callNarrow = [leg("call", -1, 0, 290), leg("call", 1, 0, 300)];
    expect(score(0.5303, callNarrow, bounded(480), bounded(-520))!).toBeCloseTo(102, 0);

    const callWide = [leg("call", -1, 0, 270), leg("call", 1, 0, 280)];
    expect(score(0.2949, callWide, bounded(795), bounded(-205))!).toBeCloseTo(144, 0);
  });

  it("generalizes past 2-leg verticals to AVGO's iron condors", () => {
    const putVertical = [leg("put", 1, 0, 390), leg("put", -1, 0, 300)];
    expect(score(0.4243, putVertical, bounded(6078), bounded(-2922))!).toBeCloseTo(131, 0);

    const condorNarrow = [
      leg("put", 1, 0, 370),
      leg("put", -1, 0, 380),
      leg("call", -1, 0, 390),
      leg("call", 1, 0, 400),
    ];
    expect(score(0.1223, condorNarrow, bounded(905), bounded(-95))!).toBeCloseTo(129, 0);

    const condorWide = [
      leg("put", 1, 0, 340),
      leg("put", -1, 0, 380),
      leg("call", -1, 0, 390),
      leg("call", 1, 0, 430),
    ];
    expect(score(0.3062, condorWide, bounded(3083), bounded(-917))!).toBeCloseTo(134, 0);
  });

  it("is null for a naked long option — the one shape the fit is known to fail", () => {
    expect(score(0.3878, [leg("put", 1, 10.5, 95)], bounded(11050), bounded(-1050))).toBeNull();
  });

  it("is null for unbounded risk unless the caller supplies a probable risk", () => {
    const straddle = [leg("call", -1, 13.3, 290), leg("put", -1, 9.25, 290)];
    expect(score(0.5809, straddle, bounded(2255), unbounded)).toBeNull();
  });

  it("extends the same formula shape when given probable risk, and never divides by zero", () => {
    const strangle = [leg("put", -1, 15.2, 360), leg("call", -1, 18.95, 385)];
    const result = score(0.6364, strangle, bounded(3415), unbounded, 6923.96);
    expect(result!).toBeCloseTo((100 * 0.6364 * (3415 + 6923.96)) / 6923.96, 6);
    expect(score(0.6364, strangle, bounded(3415), unbounded, 0)).toBeNull();
    expect(score(0.6364, strangle, bounded(3415), unbounded, null)).toBeNull();
  });
});

suite("probable risk (2 SD)", () => {
  it("matches the GOOG strangle ballpark from the disclosed methodology", () => {
    const legs = [leg("put", -1, 15.2, 360), leg("call", -1, 18.95, 385)];
    expect(probableRisk2sd(legs, 370.91, 0.3422, 74 / 365)!).toBeCloseTo(6923.96, 0);
  });

  it("is zero when a 2 SD move would still profit", () => {
    expect(probableRisk2sd([leg("put", -1, 1, 50)], 100, 0.2, 30 / 365)).toBe(0);
  });

  it("degrades on missing inputs", () => {
    const legs = [leg("put", -1, 1, 95)];
    expect(probableRisk2sd(legs, 100, Number.NaN, 30 / 365)).toBeNull();
    expect(probableRisk2sd(legs, 100, 0.3, 0)).toBeNull();
  });
});

suite("probability of worthless", () => {
  it("is the probability above the strike for a short put, and absent without a short", () => {
    const pow = probWorthless(SHORT_PUT, 100, 0.3, 25 / 365, 0.05);
    expect(pow).not.toBeNull();
    expect(pow!).toBeGreaterThan(0.5);
    expect(pow!).toBeLessThan(1);
    expect(probWorthless([leg("call", 1, 2, 105)], 100, 0.3, 25 / 365, 0.05)).toBeNull();
  });

  it("is an interval probability for a strangle — a short call can only shrink it", () => {
    const legs = [leg("put", -1, 1, 90), leg("call", -1, 1, 110)];
    const both = probWorthless(legs, 100, 0.3, 25 / 365, 0.05)!;
    const soloPut = probWorthless(legs.slice(0, 1), 100, 0.3, 25 / 365, 0.05)!;
    expect(both).toBeLessThan(soloPut);
  });
});

suite("model greeks", () => {
  it("gets a short put's signs right", () => {
    const g = bsGreeks(SHORT_PUT, 100, 0.3, 25 / 365, 0.05);
    expect(g.delta!).toBeGreaterThan(0); // short put is long delta
    expect(g.gamma!).toBeLessThan(0); // short option is short gamma
    expect(g.theta!).toBeGreaterThan(0); // collects decay
    expect(g.vega!).toBeLessThan(0); // hurt by rising IV
  });
});

suite("prose", () => {
  it("explains a short put from the payoff engine's own numbers", () => {
    const text = strategyExplanation(SHORT_PUT, 100, 0.6, "2026-08-28");
    expect(text).toContain("bullish strategy");
    expect(text).toContain("limited risk of $9,350.00"); // (95 - 1.5) * 100
    expect(text).toContain("limited potential reward of $150.00");
    expect(text).toContain("closes above $93.50 by 2026-08-28");
    expect(text).toContain("60.0% model probability");
  });

  it("says an iron condor profits BETWEEN its breakevens", () => {
    const legs = [
      leg("put", -1, 2, 95),
      leg("put", 1, 1, 90),
      leg("call", -1, 2, 105),
      leg("call", 1, 1, 110),
    ];
    const text = strategyExplanation(legs, 100, null, null);
    expect(text).toContain("neutral strategy");
    expect(text).toContain("between $93.00 and $107.00");
  });

  it("reads the greeks aloud", () => {
    const text = greeksExplanation("ON", { delta: 43.82, theta: 13.79, vega: -8.41, gamma: null })!;
    expect(text).toContain("For every $1 ON rises, this position makes about $43.82");
    expect(text).toContain("adds about $13.79 per day");
    expect(text).toContain("rise costs about $8.41");
    expect(text).toContain("Model greeks");
  });

  it("frames a short put as stock acquisition at a discount", () => {
    const text = shortPutSuggestion("U", 31, "2026-08-28", 264, 31.71);
    expect(text).toContain("$31.00 put on U");
    expect(text).toContain("net price of $28.36");
    expect(text).toContain("10.6% discount"); // (31.71 - 28.36) / 31.71
  });
});

suite("direction", () => {
  it("probes the tails, so an OTM put spread reads bullish", () => {
    // Regression for a live-caught bug: a ±10% probe landed both sides inside the max-profit
    // plateau and called this spread neutral.
    const putSpread = [leg("put", -1, 2, 47), leg("put", 1, 1, 42)];
    expect(direction(putSpread, 52.39)).toBe("bullish");
    const callSpread = [leg("call", -1, 2, 97), leg("call", 1, 1, 104)];
    expect(direction(callSpread, 96.19)).toBe("bearish");
  });
});

suite("checklists", () => {
  it("reproduces the five observed reference gradings", () => {
    // HYG-like: POW 81.39% green, annualized 6.30% green, ~100%-of-mid spread red.
    expect(statuses(checklist(0.8139, 0.063, false, 1.0))).toEqual(["pass", "pass", "pass", "fail"]);
    // SAP-like: POW 53.54% red, 20% spread red.
    expect(statuses(checklist(0.5354, 0.7845, false, 0.2))).toEqual(["fail", "pass", "pass", "fail"]);
    // DIA-like: POW 58.28% yellow, 7.5% spread yellow.
    expect(statuses(checklist(0.5828, 0.161, false, 0.075))).toEqual(["warn", "pass", "pass", "warn"]);
    // SPY-like covered call: POW 65.66% still yellow, 1% spread green.
    expect(statuses(checklist(0.6566, 0.0865, false, 0.0103))).toEqual(["warn", "pass", "pass", "pass"]);
    // HPE (live): POW 75.69% green; earnings inside the expiry warns; 48%-of-mid spread red.
    expect(statuses(checklist(0.7569, 0.3191, true, 0.48))).toEqual(["pass", "pass", "warn", "fail"]);
  });

  it("replays the four observed credit-spread cards", () => {
    // CSX: bullish put vertical, stock and market both bullish, combo spread huge.
    expect(statuses(checklistDirectional("bullish", "bullish", "bullish", false, 2.0))).toEqual([
      "pass",
      "pass",
      "pass",
      "fail",
    ]);
    // SHOP: bullish vertical against a mildly bearish stock; earnings inside.
    expect(statuses(checklistDirectional("bullish", "mildly_bearish", "bullish", true, 0.9))).toEqual([
      "fail",
      "pass",
      "warn",
      "fail",
    ]);
    // TEL: bearish vertical against a mildly bullish stock AND a bullish market.
    expect(statuses(checklistDirectional("bearish", "mildly_bullish", "bullish", false, 0.37))).toEqual([
      "fail",
      "fail",
      "pass",
      "fail",
    ]);
    // DIS: bearish vertical with a bearish stock trend, against the bullish market.
    expect(statuses(checklistDirectional("bearish", "bearish", "bullish", null, null))).toEqual([
      "pass",
      "fail",
      "warn",
      "warn",
    ]);
  });

  it("warns on a neutral or unknown trend rather than guessing a side", () => {
    expect(statuses(checklistDirectional("bullish", "neutral", null, false, 0.02))).toEqual([
      "warn",
      "warn",
      "pass",
      "pass",
    ]);
  });

  it("caps a tight spread at warn without weekly cadence", () => {
    // Suite rule: high liquidity must always have weekly expirations available.
    const grade = (items: Array<{ name: string; status: string }>): string =>
      items.find((i) => i.name === "Spread & liquidity")!.status;
    expect(grade(checklist(0.8, 0.1, false, 0.02, false))).toBe("warn");
    expect(grade(checklist(0.8, 0.1, false, 0.02, true))).toBe("pass");
    expect(grade(checklist(0.8, 0.1, false, 0.02))).toBe("pass"); // cadence unknown: spread stands alone
    expect(grade(checklistDirectional("bullish", null, null, false, 0.3, true))).toBe("fail");
  });

  it("warns on unknowns rather than passing them", () => {
    expect(statuses(checklist(null, null, null, null)).every((s) => s === "warn")).toBe(true);
    expect(statuses(checklist(0.45, 0.01, true, 0.3))).toEqual(["fail", "fail", "warn", "fail"]);
  });
});

suite("weekly cadence", () => {
  it("requires a real weekly gap, not merely a near expiration", () => {
    expect(hasWeeklyCadence(["2026-08-28", "2026-09-04", "2026-09-18", "2026-10-16"])).toBe(true);
    expect(hasWeeklyCadence(["2026-08-21", "2026-09-18", "2026-10-16", "2027-01-15"])).toBe(false);
    expect(hasWeeklyCadence([])).toBe(false);
    expect(hasWeeklyCadence(["garbage"])).toBe(false);
  });
});

suite("combo spread", () => {
  it("grades the NET strategy price, and refuses to grade a one-sided quote", () => {
    // The observed CSX card graded combo bid $0.00 / ask $1.30, not per-leg widths.
    const legs = [
      { quantity: -1, bid: 2.0, ask: 2.2 },
      { quantity: 1, bid: 1.0, ask: 1.1 },
    ];
    const pct = comboSpreadPct(legs)!;
    expect(pct).toBeGreaterThan(0);
    expect(comboSpreadPct([{ quantity: -1, bid: null, ask: 2.2 }])).toBeNull();
  });
});
