import { describe, expect, it } from "vitest";
import { etaText, progressLine } from "../src/pages/Backtest/QueueTab";

const p = { total: 97402, answered: 62202, byStatus: { complete: 61617 }, ratePerMin: 232.7, etaMinutes: 151, lastAnswerAgeS: 1 };

describe("the queue's fetch meter", () => {
  it("states counts, share, rate and time left", () => {
    expect(progressLine(p)).toBe("62,202 of 97,402 (63.9%) · 233/min · ~2h 31m left");
    expect(etaText(12)).toBe("12m");
    expect(etaText(0)).toBe("done");
  });

  it("says stalled when nothing was answered for ten minutes, not started when nothing ever was", () => {
    expect(progressLine({ ...p, ratePerMin: null, etaMinutes: null, lastAnswerAgeS: 900 })).toContain("stalled");
    expect(progressLine({ ...p, answered: 0, ratePerMin: null, etaMinutes: null, lastAnswerAgeS: null })).toContain(
      "not started",
    );
  });
});
