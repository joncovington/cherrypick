import { describe, expect, it } from "vitest";
import type { LiveMeicOpenRow } from "@console/shared";
import { meicOpenMark } from "../src/readers/liveToday.js";

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
    // (180 − 70) + (200 − 260) = 50; the working entry adds nothing.
    expect(m).toMatchObject({ value: 50, at: "2030-01-02T17:44:10.000Z" });
    expect(m!.basis).toContain("before fees");
  });

  it("is n/r when any held condor is unmarked, and nothing when nothing is held", () => {
    expect(meicOpenMark([row({ mark: mark(-70, "2030-01-02T17:44:30.000Z") }), row({ id: 2, putHeld: false })])!.value).toBeNull();
    expect(meicOpenMark([row({ working: true, entryCash: null })])).toMatchObject({ value: null, at: null });
    expect(meicOpenMark([])).toMatchObject({ value: null, at: null });
  });
});
