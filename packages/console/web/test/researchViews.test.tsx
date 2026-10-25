import { describe, expect, it } from "vitest";
import type { ExecutionModule, ExecutionWeek, SliceCell } from "@console/shared";
import { gapLine, overrideLine } from "../src/pages/Live/ExecutionTab";
import { fmtCi, fmtMean } from "../src/pages/Backtest/SlicesTab";

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
