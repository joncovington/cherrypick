import { describe, expect, it } from "vitest";
import { shapeQueue, toProgress } from "../src/services/researchQueueBridge.js";

const view = {
  file: "q3",
  job: "backtest-validate-meic",
  state: "running",
  phases: [
    { name: "decisions", done: 1, total: 3, unit: "replays", current: "vendor" },
    { name: "sessions", done: 2, total: 4, unit: "days", current: "2026-10-15" },
  ],
  fraction: 0.5,
  eta_s: 1800,
  age_s: 4,
  line: "decisions 1/3 replays (vendor) · sessions 2/4 days (2026-10-15) · ~00:30 remaining",
  note: null,
  started_at: 1000,
  updated_at: 1600,
};

describe("a job's progress (core.progress)", () => {
  it("renames the orchestrator's view and keeps every nested phase", () => {
    const p = toProgress(view);
    expect(p).toMatchObject({ file: "q3", state: "running", fraction: 0.5, etaSeconds: 1800, ageSeconds: 4 });
    expect(p?.phases.map((ph) => `${ph.name} ${ph.done}/${ph.total} ${ph.current}`)).toEqual([
      "decisions 1/3 vendor",
      "sessions 2/4 2026-10-15",
    ]);
  });

  it("refuses a shapeless one and an unknown state", () => {
    expect(toProgress(null)).toBeNull();
    expect(toProgress({ job: "x" })).toBeNull();
    expect(toProgress({ ...view, state: "sleeping" })).toBeNull();
  });

  it("carries runs started by hand beside the queue's tasks", () => {
    const res = shapeQueue({
      ok: true,
      error: null,
      json: { ok: true, session_held: false, blackout: null, tasks: [], by_hand: [{ ...view, file: "backtest-run-meic-77" }] },
    });
    expect(res.ok && res.data?.byHand.map((p) => p.file)).toEqual(["backtest-run-meic-77"]);
  });
});
