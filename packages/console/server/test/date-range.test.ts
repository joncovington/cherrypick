import { describe, expect, it } from "vitest";
import { inRange, parseDateRange, rangeClauses } from "../src/readers/dateRange.js";

/** The history tables' shared date range (readers/dateRange.ts). */
describe("dateRange", () => {
  it("parses only ISO dates, so a malformed bound cannot silently match nothing", () => {
    expect(parseDateRange({ from: "2026-09-01", to: "Sept 5" })).toEqual({ from: "2026-09-01", to: null });
    expect(parseDateRange(undefined)).toEqual({ from: null, to: null });
  });

  it("compares a timestamp column by its date", () => {
    expect(rangeClauses("closed_at", { from: "2026-09-01", to: "2026-09-05" })).toEqual({
      clauses: ["substr(closed_at, 1, 10) >= ?", "substr(closed_at, 1, 10) <= ?"],
      params: ["2026-09-01", "2026-09-05"],
    });
    expect(rangeClauses("week_of", { from: null, to: null }).clauses).toEqual([]);
  });

  it("is inclusive at both ends, and a row with no date is outside any set bound", () => {
    const r = { from: "2026-09-01", to: "2026-09-05" };
    expect(inRange("2026-09-01T09:30:00Z", r)).toBe(true);
    expect(inRange("2026-09-05", r)).toBe(true);
    expect(inRange("2026-09-06", r)).toBe(false);
    expect(inRange(null, r)).toBe(false);
    expect(inRange(null, { from: null, to: null })).toBe(true);
  });
});
