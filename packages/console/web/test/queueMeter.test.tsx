import { describe, expect, it } from "vitest";
import { progressLine } from "../src/pages/Backtest/QueueTab";

const p = {
  file: "q3",
  job: "backtest-validate-meic",
  state: "running" as const,
  phases: [{ name: "sessions", done: 2, total: 4, unit: "days", current: "2026-10-15" }],
  fraction: 0.5,
  etaSeconds: 600,
  ageSeconds: 3,
  line: "sessions 2/4 days (2026-10-15) · ~10 min left",
  note: null,
  startedAt: 1000,
  updatedAt: 1600,
};

describe("the queue's progress meter", () => {
  it("states the overall share and the job's own reading", () => {
    expect(progressLine(p)).toBe("50.0% · sessions 2/4 days (2026-10-15) · ~10 min left");
  });

  it("says how a finished job ended, and its note", () => {
    expect(progressLine({ ...p, state: "failed", note: "no official close" })).toContain("failed · no official close");
  });

  it("reads a job with no totals by its line alone", () => {
    expect(progressLine({ ...p, fraction: null, line: "working 7" })).toBe("working 7");
  });
});
