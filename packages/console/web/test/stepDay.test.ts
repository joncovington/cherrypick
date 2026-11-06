import { describe, it, expect } from "vitest";
import { stepDay } from "../src/lib/stepDay";

/**
 * The trade-summary day walk (MeicLightbox's day arrows), pinned as a pure function. The seam it
 * walks: `daily_summary` rows are written once per session after the close, so between the first
 * entry and the roll-up the current day has no row in the summaries list while the header still
 * names it. The house rule: a new guard is shown to fail by breaking its invariant on purpose —
 * the pre-fix walk (an indexOf with an early return at -1) fails "back onto the session" below,
 * which is the point.
 */

// Five settled sessions, newest last (the walk sorts, input order free). The live session,
// 2026-11-06, has traded but not rolled up: no row in the list.
const DAYS = ["2026-11-05", "2026-11-03", "2026-11-04", "2026-10-30", "2026-11-02"];
const SESSION = "2026-11-06";

describe("stepDay", () => {
  it("steps back from the un-rolled-up session onto the newest settled day", () => {
    expect(stepDay(DAYS, SESSION, SESSION, -1)).toBe("2026-11-05");
  });

  it("steps forward from the newest settled day back onto the session (null = latest session)", () => {
    expect(stepDay(DAYS, "2026-11-05", SESSION, 1)).toBeNull();
  });

  it("steps forward from an older day onto the next settled day", () => {
    expect(stepDay(DAYS, "2026-11-03", SESSION, 1)).toBe("2026-11-04");
  });

  it("steps back from an older day onto the previous settled day", () => {
    expect(stepDay(DAYS, "2026-11-04", SESSION, -1)).toBe("2026-11-03");
  });

  it("clamps at the oldest day: the previous arrow holds", () => {
    expect(stepDay(DAYS, "2026-10-30", SESSION, -1)).toBe("2026-10-30");
  });

  it("on the live session the next arrow stays on latest session (null, the slot it already occupies)", () => {
    expect(stepDay(DAYS, SESSION, SESSION, 1)).toBeNull();
  });

  it("with no resolvable session, forward from the newest day stays put", () => {
    expect(stepDay(DAYS, "2026-11-05", null, 1)).toBe("2026-11-05");
  });

  it("deduplicates a list that carries a day more than once (per-symbol summary rows)", () => {
    expect(stepDay(["2026-11-05", "2026-11-05", "2026-11-04"], "2026-11-05", SESSION, -1)).toBe(
      "2026-11-04",
    );
  });
});
