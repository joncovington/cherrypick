/**
 * The bucketing rule two pages must agree on.
 *
 * flies and meic each carried a byte-identical copy of this under a different name. Two copies of
 * a date-bucketing rule drifting on where a week starts would group the same sessions differently
 * on two pages, and both would look right.
 */

import { describe, expect, it } from "vitest";

import { PERIODS, equityCurve, median, periodKey, riskSummary, stdev } from "../src/analytics/riskMetrics.js";

describe("periodKey", () => {
  it("returns the day itself for daily", () => {
    expect(periodKey("daily", "2026-08-20")).toBe("2026-08-20");
    expect(periodKey("anything-else", "2026-08-20")).toBe("2026-08-20");
  });

  it("returns the ISO month prefix for monthly", () => {
    expect(periodKey("monthly", "2026-08-20")).toBe("2026-08");
  });

  it("anchors weeks on MONDAY, not Sunday", () => {
    // SQLite's %W anchors on Sunday, which puts a boundary through the middle of a trading week.
    // 2026-08-17 is a Monday; every session that week must land on it.
    for (const day of ["2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21"]) {
      expect(periodKey("weekly", day)).toBe("2026-08-17");
    }
  });

  it("puts a Sunday with the week that FOLLOWS it, not the trading week before", () => {
    // The Monday anchor means Sunday 2026-08-16 belongs to the week starting 2026-08-10.
    expect(periodKey("weekly", "2026-08-16")).toBe("2026-08-10");
    expect(periodKey("weekly", "2026-08-15")).toBe("2026-08-10"); // Saturday, same week
  });

  it("crosses a month boundary without splitting the week", () => {
    expect(periodKey("weekly", "2026-09-01")).toBe("2026-08-31");
    expect(periodKey("weekly", "2026-08-31")).toBe("2026-08-31");
  });
});

describe("median", () => {
  it("is null for an empty set", () => {
    expect(median([])).toBeNull();
  });

  it("takes the middle of an odd count", () => {
    expect(median([3, 1, 2])).toBe(2);
  });

  it("averages the two middles of an even count", () => {
    expect(median([4, 1, 3, 2])).toBe(2.5);
  });

  it("does not mutate its input", () => {
    const xs = [3, 1, 2];
    median(xs);
    expect(xs).toEqual([3, 1, 2]);
  });
});

describe("stdev", () => {
  it("is null below two values, never zero", () => {
    // One observation has no dispersion; reporting 0 would be the misleadingly-precise zero the
    // suite's ledgers already refuse to write.
    expect(stdev([])).toBeNull();
    expect(stdev([5])).toBeNull();
  });

  it("uses the sample denominator (n-1)", () => {
    // [2,4,4,4,5,5,7,9] has population sd 2 and sample sd ~2.138.
    expect(stdev([2, 4, 4, 4, 5, 5, 7, 9])).toBeCloseTo(2.13809, 4);
  });

  it("is zero for identical values", () => {
    expect(stdev([3, 3, 3])).toBe(0);
  });
});

describe("annualization", () => {
  const curve = equityCurve([
    { date: "2026-08-03", net: 400 },
    { date: "2026-08-04", net: -200 },
    { date: "2026-08-05", net: 600 },
    { date: "2026-08-06", net: -100 },
    { date: "2026-08-07", net: 300 },
  ]);

  it("defaults to trading sessions", () => {
    expect(riskSummary(curve).sharpe).toBe(riskSummary(curve, PERIODS.TRADING_SESSIONS).sharpe);
  });

  it("scales Sharpe by sqrt(periods), so a WEEKLY series is not read as a daily one", () => {
    // The trap this parameter exists for: handing a weekly curve the daily constant inflates the
    // ratio by sqrt(252/52) ~ 2.2x, and nothing about the number would look wrong.
    const daily = riskSummary(curve, PERIODS.TRADING_SESSIONS).sharpe;
    const weekly = riskSummary(curve, PERIODS.TRADING_WEEKS).sharpe;
    expect(daily).not.toBeNull();
    expect(weekly).not.toBeNull();
    // 2dp, not 6: riskSummary rounds each ratio to 3dp, so a ratio OF two rounded values
    // carries that noise. The scaling is exact; the stored precision is not.
    expect(daily! / weekly!).toBeCloseTo(Math.sqrt(252 / 52), 2);
  });

  it("scales Sortino the same way", () => {
    const daily = riskSummary(curve, PERIODS.TRADING_SESSIONS).sortino;
    const weekly = riskSummary(curve, PERIODS.TRADING_WEEKS).sortino;
    // 2dp, not 6: riskSummary rounds each ratio to 3dp, so a ratio OF two rounded values
    // carries that noise. The scaling is exact; the stored precision is not.
    expect(daily! / weekly!).toBeCloseTo(Math.sqrt(252 / 52), 2);
  });

  it("scales Calmar linearly, not by the root — it annualizes a RETURN, not a deviation", () => {
    const daily = riskSummary(curve, PERIODS.TRADING_SESSIONS).calmar;
    const weekly = riskSummary(curve, PERIODS.TRADING_WEEKS).calmar;
    expect(daily! / weekly!).toBeCloseTo(252 / 52, 2);
  });

  it("leaves recovery factor and sample size alone — neither is annualized", () => {
    const daily = riskSummary(curve, PERIODS.TRADING_SESSIONS);
    const weekly = riskSummary(curve, PERIODS.TRADING_WEEKS);
    expect(daily.recoveryFactor).toBe(weekly.recoveryFactor);
    expect(daily.sampleSize).toBe(weekly.sampleSize);
  });
});
