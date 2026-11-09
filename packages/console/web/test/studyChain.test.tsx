import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { StudyResult, StudyRow, StudyTask } from "@console/shared";
import { StudyChain, studyChain } from "../src/pages/Backtest/StudyCards";

const task = (id: number, kind: string, step: string, status: string): StudyTask => ({
  id,
  kind,
  step,
  status,
  createdAt: null,
  startedAt: null,
  finishedAt: null,
});

function row(over: Partial<StudyRow>): StudyRow {
  return {
    id: 69,
    study: "item69",
    title: "A study",
    status: "declared",
    legacy: false,
    pointer: "docs/edge-hypotheses-plan.md#69-a-study",
    outcome: null,
    measure: "net",
    expected: "> 0",
    sample: "2022-2026",
    rule: "t > 2",
    mechanism: "m",
    rivals: [],
    scripts: ["scripts/a_study_item69.py"],
    tests: ["packages/backtest/tests/test_a_study_item69_script.py"],
    group: "declared",
    results: [],
    queueActive: false,
    tasks: [],
    lastActivity: null,
    ...over,
  };
}

const result = { name: "study-design.json", present: true, window: "design", span: null, verdicts: [{ arm: "a", verdict: "fail" }] } as unknown as StudyResult;

describe("study chain", () => {
  it("lists a declared study's steps in order, the run still to come", () => {
    const steps = studyChain(row({}));
    expect(steps.map((s) => s.key)).toEqual(["declaration", "fetch", "code", "runs", "results", "verdict"]);
    expect(steps.map((s) => s.state)).toEqual(["done", "none", "done", "pending", "pending", "pending"]);
    expect(steps[2]!.links.map((l) => l.text)).toEqual(["a_study_item69.py", "test_a_study_item69_script.py"]);
  });

  it("reads fetch and run steps from the queue, oldest first, cancelled left out", () => {
    const steps = studyChain(
      row({
        tasks: [
          task(12, "script", "holdout", "queued"),
          task(10, "fetch-chain-paths", "fetch", "done"),
          task(11, "script", "design", "running"),
          task(9, "script", "design", "cancelled"),
        ],
      }),
    );
    expect(steps.map((s) => s.label)).toEqual(["Declaration", "Data", "Script and tests", "design", "holdout", "Results", "Verdict"]);
    expect(steps.find((s) => s.key === "fetch")!.state).toBe("done");
    expect(steps.filter((s) => s.key.startsWith("step-")).map((s) => s.state)).toEqual(["running", "queued"]);
  });

  it("marks what a finished study is missing, and spares a legacy row", () => {
    const done = studyChain(row({ status: "failed", outcome: "fails its design", results: [result], scripts: [], tests: [] }));
    expect(done.find((s) => s.key === "code")!.state).toBe("missing");
    expect(done.find((s) => s.key === "results")!.detail).toBe("design fail");
    expect(done.find((s) => s.key === "verdict")!.state).toBe("done");
    const legacy = studyChain(row({ legacy: true, scripts: [], tests: [] }));
    expect(legacy.find((s) => s.key === "code")!.state).toBe("none");
  });

  it("renders each step with its state", () => {
    const html = renderToString(<StudyChain row={row({})} />);
    expect(html).toContain("Script and tests");
    expect(html).toContain("a_study_item69.py");
  });
});
