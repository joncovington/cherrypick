import { describe, expect, it } from "vitest";
import { shapeReport } from "../src/services/studyReportBridge.js";

const arm = (extra: Record<string, unknown>) => ({ arm: "a", group: "arms", window: "design", file: "r.json", mean: 1, ...extra });

describe("study report scorecard", () => {
  it("carries an arm's scorecard and drops malformed insight lines", () => {
    const card = {
      stats: { total_pl: 120, max_drawdown: 40, return_on_dd: 3 },
      insights: [
        { key: "longest_drawdown", text: "Longest drawdown lasted 9 days.", flag: true },
        { key: "broken" },
      ],
    };
    const r = shapeReport({ ok: true, error: null, json: { ok: true, arms: [arm({ scorecard: card })] } });
    expect(r.ok).toBe(true);
    const sc = r.data!.arms[0]!.scorecard!;
    expect(sc.stats.total_pl).toBe(120);
    expect(sc.insights).toEqual([{ key: "longest_drawdown", text: "Longest drawdown lasted 9 days.", flag: true }]);
  });

  it("reads null for an arm without one or with another shape", () => {
    const r = shapeReport({
      ok: true,
      error: null,
      json: { ok: true, arms: [arm({}), arm({ arm: "b", scorecard: { stats: {}, insights: [] } }), arm({ arm: "c", scorecard: { stats: { x: 1 } } })] },
    });
    expect(r.data!.arms.map((a) => a.scorecard)).toEqual([null, null, null]);
  });
});
