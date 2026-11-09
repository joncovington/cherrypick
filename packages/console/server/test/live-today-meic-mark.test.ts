import { describe, expect, it } from "vitest";
import type { LiveMeicOpenRow } from "@console/shared";
import type { LiveTodayLoop } from "@console/shared";
import { meicOpenMark, unrealisedOf } from "../src/readers/liveToday.js";

/**
 * Live > today's MEIC mark (2026-11-07): the open condors' stream-cache marks summed as whole-position
 * cash. A part-marked book is n/r, never a part-sum that reads as the whole; a working entry holds
 * nothing and is left out; nothing held is no mark.
 */
const row = (over: Partial<LiveMeicOpenRow> = {}): LiveMeicOpenRow => ({
  id: 1,
  tradeDate: "2030-01-02",
  entryTime: "2030-01-02T12:30:00",
  symbol: "XYZ",
  arm: "a",
  status: "open",
  working: false,
  putStrike: 100,
  callStrike: 120,
  wingWidth: 10,
  quantity: 1,
  netCredit: 1.8,
  entryCash: 180,
  putHeld: true,
  callHeld: true,
  putClosing: false,
  callClosing: false,
  fillConfirmedAt: null,
  overdue: false,
  mark: null,
  feesToDate: 4,
  ...over,
});
const mark = (exitCash: number, at: string) => ({ at, mids: { shortPut: 0, longPut: 0, shortCall: 0, longCall: 0 }, closeCost: -exitCash / 100, exitCash });

describe("MEIC's mark on Live > today", () => {
  it("sums entry credit and the close at the mark over every held condor, as of the oldest quote", () => {
    const m = meicOpenMark([
      row({ id: 1, mark: mark(-70, "2030-01-02T17:44:30.000Z") }),
      row({ id: 2, entryCash: 200, mark: mark(-260, "2030-01-02T17:44:10.000Z") }),
      row({ id: 3, working: true, entryCash: null }),
    ]);
    // (180 − 70 − 4) + (200 − 260 − 4) = 42: net of each condor's fees; the working entry adds nothing.
    expect(m).toMatchObject({ value: 42, at: "2030-01-02T17:44:10.000Z" });
    expect(m!.basis).toContain("fees to date");
  });

  it("is n/r when any held condor is unmarked, and nothing when nothing is held", () => {
    expect(meicOpenMark([row({ mark: mark(-70, "2030-01-02T17:44:30.000Z") }), row({ id: 2, putHeld: false })])!.value).toBeNull();
    expect(meicOpenMark([row({ working: true, entryCash: null })])).toMatchObject({ value: null, at: null });
    expect(meicOpenMark([])).toMatchObject({ value: null, at: null });
  });

  it("is n/r when a held condor's fees were never recorded, never a figure before fees", () => {
    expect(meicOpenMark([row({ feesToDate: null, mark: mark(-70, "2030-01-02T17:44:30.000Z") })])!.value).toBeNull();
  });
});

const loop = (id: LiveTodayLoop["loop"], over: Partial<LiveTodayLoop>): LiveTodayLoop =>
  ({ loop: id, ledger: "ok", open: 0, mark: null, ...over }) as LiveTodayLoop;

describe("the suite's unrealised total", () => {
  it("sums every loop holding something at its mark, as of the oldest, and adds nothing for a loop holding nothing", () => {
    const u = unrealisedOf([
      loop("flies", { open: 1, mark: { value: -20.5, at: "2030-01-02T17:44:30Z", basis: "" } }),
      loop("meic", { open: 2, mark: { value: 50.25, at: "2030-01-02T17:44:10Z", basis: "" } }),
      // a stale mark on a loop holding nothing is not open money
      loop("bwb", { open: 0, mark: { value: 999, at: "2030-01-02T10:00:00Z", basis: "" } }),
    ]);
    expect(u).toEqual({ value: 29.75, at: "2030-01-02T17:44:10Z", summed: ["flies", "meic"], unmarked: [] });
  });

  it("is n/r, naming why, when a loop holds what it cannot mark or its ledger could not be read", () => {
    const u = unrealisedOf([
      loop("flies", { open: 1, mark: { value: -20, at: null, basis: "" } }),
      loop("bwb", { open: 1, mark: { value: null, at: null, basis: "" } }),
      loop("meic", { ledger: "failed" }),
    ]);
    expect(u.value).toBeNull();
    expect(u.unmarked).toEqual([
      { loop: "bwb", reason: "1 open, not all marked" },
      { loop: "meic", reason: "the live ledger could not be read" },
    ]);
  });

  it("is zero, not n/r, when nothing is open anywhere, and leaves an absent ledger out", () => {
    expect(unrealisedOf([loop("flies", {}), loop("bwb", { ledger: "absent" })])).toEqual({ value: 0, at: null, summed: [], unmarked: [] });
  });
});
