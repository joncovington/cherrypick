import { describe, expect, it } from "vitest";
import type { ExecutionModule, ExecutionWeek, SliceCell, SliceDimensionRead, SliceReport } from "@console/shared";
import { gapLine, overrideLine, windowLine } from "../src/pages/Live/ExecutionTab";
import { dimensionLabel, dimensionSentence, fmtCi, fmtMean, overallSentence } from "../src/pages/Backtest/SlicesTab";

const week = (over: Partial<ExecutionWeek>): ExecutionWeek => ({
  week: "2026-W42",
  net: 0,
  trades: 0,
  orders: 0,
  fillRate: null,
  slipDollars: null,
  slipPctWidthMean: null,
  minutesToFillMean: null,
  overrides: [],
  clean: true,
  ...over,
});

describe("the execution tab's lines", () => {
  it("names a clean week clean and counts each override kind", () => {
    expect(overrideLine(week({}))).toBe("clean");
    const kinds = [{ kind: "manual_close", session: null, at: null }, { kind: "manual_close", session: null, at: null }, { kind: "kill_cleared", session: null, at: null }];
    expect(overrideLine(week({ clean: false, overrides: kinds }))).toBe("manual close ×2, kill cleared");
  });

  it("states the gap with its parts and the largest, or nothing without one", () => {
    const m: ExecutionModule = {
      module: "meic",
      ok: true,
      reason: null,
      mirror: "live-shadow",
      weeks: [],
      gap: { since: "2026-10-15", sessions: 3, total: -133.17, parts: { missed: -172.48 }, largest: "missed" },
      window: null,
    };
    expect(gapLine(m)).toBe("live − live-shadow since 2026-10-15 over 3 working sessions: -$133.17 (missed -$172.48); largest: missed");
    expect(gapLine({ ...m, gap: null })).toBeNull();
  });
});

describe("the slices tab's figures", () => {
  const c: SliceCell = { value: "uptrend", n: 35, mean: 0.0512, ci95: [-0.1, 0.2], greyed: false };
  it("reads R to three places and dollars to cents", () => {
    expect(fmtMean(c, "R")).toBe("0.051R");
    expect(fmtMean({ ...c, mean: 12.5 }, "net $")).toBe("$12.50");
    expect(fmtCi(c, "R")).toBe("-0.100 to 0.200");
    expect(fmtCi({ ...c, ci95: null }, "R")).toBe("—");
  });
});

describe("the slices tab's plain-English card", () => {
  const cell = (value: string, mean: number, lo: number, hi: number): SliceCell => ({ value, n: 500, mean, ci95: [lo, hi], greyed: false });
  const read = (r: SliceDimensionRead["read"], best: SliceCell | null, worst: SliceCell | null): SliceDimensionRead => ({
    name: "entry_hour",
    read: r,
    best,
    worst,
    ranked: 2,
    greyed: 0,
  });

  it("says a separated dimension matters, with both groups and their averages", () => {
    const s = dimensionSentence(read("separated", cell("13:00", 0.1764, 0.169, 0.1838), cell("09:00", 0.0504, 0.0166, 0.0842)), "R");
    expect(s).toBe(
      "This one matters. 13:00 averaged 0.176R a trade against 0.050R a trade for 09:00, and their likely ranges do not overlap, so the gap is unlikely to be chance in these trades.",
    );
  });

  it("says an overlapping dimension could be chance, and says nothing where there is nothing to compare", () => {
    expect(dimensionSentence(read("overlapping", cell("uptrend", 0.12, 0.1, 0.13), cell("mixed", 0.11, 0.1, 0.12)), "R")).toContain("could be chance");
    expect(dimensionSentence(read("one-group", cell("same day", 0.1, 0.05, 0.15), null), "R")).toBe(
      "Every trade fell in one group (same day), so there is nothing to compare here.",
    );
    expect(dimensionSentence(read("too-few", null, null), "R")).toContain("says nothing yet");
  });

  it("puts the overall result against zero in words, in the report's unit", () => {
    const base: SliceReport = {
      file: "a.json",
      label: "meic_ic/control/all",
      unit: "R",
      trades: 12105,
      cells: 13,
      rankedCells: 13,
      overall: cell("all", 0.1186, 0.1132, 0.124),
      dimensions: [],
      note: "",
      analysis: { schema: "meic_ic", arm: "control", month: null, overall: "positive", dimensions: [], greyedCells: 0 },
    };
    expect(overallSentence(base)).toBe(
      "Across all 12,105 trades the average was 0.119R a trade, and the true average very likely sits between 0.113R and 0.124R. That whole range is above zero, so on these trades the arm made money.",
    );
    expect(overallSentence({ ...base, analysis: { ...base.analysis, overall: "unclear" } })).toContain("breaking even");
    expect(dimensionLabel("entry_hour")).toBe("The hour of the day the trade was entered (Eastern time)");
    expect(dimensionLabel("new_thing")).toBe("new thing");
  });
});

describe("the window trigger line", () => {
  const base: ExecutionModule = { module: "meic", ok: true, reason: null, mirror: "live-shadow", weeks: [], gap: null, window: null };
  it("says it is reported only, and how far along it is", () => {
    expect(windowLine({ ...base, window: { window: 50, n: 3, mean: 20, se: null, baseline: null, z: null, would: "too_few" } })).toBe(
      "window trigger (reported, never enforced): 3 of 50 live trades so far",
    );
    expect(
      windowLine({ ...base, window: { window: 50, n: 50, mean: -20, se: 1.4, baseline: 10, z: -21.2, would: "alarm" } }),
    ).toContain("would read alarm");
    expect(windowLine(base)).toBeNull();
  });
});
