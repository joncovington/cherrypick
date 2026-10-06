import { describe, expect, it } from "vitest";
import type { DatedValue } from "@console/shared";
import { inWindow, inversionWindows, rebase, rolling, underwater } from "../src/lib/navSeries";

const NAV: DatedValue[] = [
  ["2026-01-02", 100],
  ["2026-01-05", 120],
  ["2026-01-06", 90],
  ["2026-01-07", 130],
];

describe("navSeries", () => {
  it("underwater is a fraction below peak for a NAV, dollars from zero for equity", () => {
    expect(underwater(NAV, "nav").map((p) => p.y)).toEqual([0, 0, -0.25, 0]);
    // equity's peak starts at 0, so a first-day loss is already a drawdown
    expect(underwater([["a", -5], ["b", 10], ["c", 4]], "equity").map((p) => p.y)).toEqual([-5, 0, -6]);
  });

  it("rolling starts once the window is full", () => {
    const r = rolling(NAV, 2, "nav");
    expect(r.map((p) => p.x)).toEqual(["2026-01-06", "2026-01-07"]);
    expect(r[0]!.y).toBeCloseTo(-0.1);
    expect(rolling(NAV, 2, "equity")[1]!.y).toBe(10);
  });

  it("rebase starts every series at the same value", () => {
    expect(rebase(NAV, 10_000).map((p) => p.y)).toEqual([10_000, 12_000, 9_000, 13_000]);
  });

  it("finds inversion stretches, including one still open", () => {
    const rows = [
      { tradeDate: "d1", ratio: 0.9 },
      { tradeDate: "d2", ratio: 1.02 },
      { tradeDate: "d3", ratio: 1.05 },
      { tradeDate: "d4", ratio: 0.95 },
      { tradeDate: "d5", ratio: 1.01 },
    ];
    const w = inversionWindows(rows);
    expect(w.map((x) => [x.from, x.to])).toEqual([
      ["d2", "d3"],
      ["d5", "d5"],
    ]);
    expect(w[1]!.label).toContain("open");
  });

  it("a window's change counts its first day, and a series with no point in it is not held", () => {
    const w = { label: "x", from: "2026-01-05", to: "2026-01-06", source: "fixed" as const };
    const r = inWindow(NAV, w, "nav");
    expect(r.change).toBeCloseTo(-0.1); // from the 100 before the window to 90
    expect(r.drawdown).toBeCloseTo(-0.25); // 120 down to 90
    expect(inWindow(NAV, { ...w, from: "2025-01-01", to: "2025-02-01" }, "nav").points).toBe(0);
  });
});
