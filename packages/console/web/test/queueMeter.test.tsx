import { describe, expect, it } from "vitest";
import { etaText, progressLine } from "../src/pages/Backtest/QueueTab";

const p = { total: 10000, answered: 6000, byStatus: { complete: 5900 }, ratePerMin: 200.4, etaMinutes: 20, lastAnswerAgeS: 1 };

describe("the queue's fetch meter", () => {
  it("states counts, share, rate and time left", () => {
    expect(progressLine(p)).toBe("6,000 of 10,000 (60.0%) · 200/min · ~20m left");
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
