import { afterEach, describe, expect, it } from "vitest";
import { readScorecard, setScorecardCaller } from "../src/readers/scorecard.js";

/** The scorecard: the orchestrator's reply passed through, its failure named. */

afterEach(() => setScorecardCaller());

describe("readScorecard", () => {
  it("passes the rows, looks and counts through", async () => {
    const row = { module: "meic", arm: "x", look: "decision_due", look_words: "a decision against its control is due" };
    setScorecardCaller(() => ({
      ok: true,
      error: null,
      json: { ok: true, rows: [row], eras: { meic: { status: "ok", start: "2026-11-03" } }, looks: [{ key: "decision_due", words: "w" }], counts: { decision_due: 1 }, min_sessions: 10, modules: ["meic"] },
    }));
    const out = await readScorecard();
    expect(out.error).toBeNull();
    expect(out.rows).toEqual([row]);
    expect(out.counts).toEqual({ decision_due: 1 });
    expect(out.eras["meic"]?.start).toBe("2026-11-03");
    expect(out.min_sessions).toBe(10);
  });

  it("names a refusal or a failed spawn", async () => {
    setScorecardCaller(() => ({ ok: true, error: null, json: { ok: false, error: "no register" } }));
    expect((await readScorecard()).error).toBe("no register");
    setScorecardCaller(() => ({ ok: false, error: "timed out", json: null }));
    const out = await readScorecard();
    expect(out.error).toBe("timed out");
    expect(out.rows).toEqual([]);
  });
});
