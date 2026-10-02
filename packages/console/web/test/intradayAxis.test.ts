import { describe, expect, it } from "vitest";
import type { Time } from "lightweight-charts";
import { etTickMark } from "../src/pages/Intraday/IntradayPage";

/**
 * The live chart's time axis reads on the ET session clock. lightweight-charts places its day ticks
 * at UTC midnight — 20:00 ET in summer — and dating that tick put "Oct 1" at 20:00 on Oct 1 (seen in
 * the first browser check, 2026-10-01).
 */

const at = (iso: string): Time => (Date.parse(iso) / 1000) as Time;

describe("the intraday time axis", () => {
  it("a UTC-midnight tick is labelled with its ET time, not a date", () => {
    expect(etTickMark(at("2026-10-02T00:00:00Z"))).toBe("20:00");
    expect(etTickMark(at("2026-12-02T00:00:00Z"))).toBe("19:00"); // EST
  });

  it("ET midnight carries the ET date", () => {
    expect(etTickMark(at("2026-10-02T04:00:00Z"))).toBe("Oct 2");
  });

  it("an ordinary tick is the ET time", () => {
    expect(etTickMark(at("2026-10-02T03:50:00Z"))).toBe("23:50");
  });
});
