import { describe, expect, it } from "vitest";
import { toProgress } from "../src/services/researchQueueBridge.js";

describe("a fetch row's progress", () => {
  it("renames the orchestrator's counts and refuses a shapeless one", () => {
    expect(
      toProgress({ total: 10, answered: 4, by_status: { complete: 3, empty: 1 }, rate_per_min: 2, eta_minutes: 3, last_answer_age_s: 5 }),
    ).toEqual({ total: 10, answered: 4, byStatus: { complete: 3, empty: 1 }, ratePerMin: 2, etaMinutes: 3, lastAnswerAgeS: 5 });
    expect(toProgress(null)).toBeNull();
    expect(toProgress({ total: 10 })).toBeNull();
  });
});
