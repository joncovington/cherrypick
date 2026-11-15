import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import Fastify, { type FastifyInstance } from "fastify";
import { registerBacktestRoutes } from "../src/routes/backtest.js";
import { setBridgeCaller, type BridgeResult } from "../src/services/configBridge.js";
import { invalidateFeatures } from "../src/services/featuresBridge.js";
import { resetStudyReportCache, setStudyReportCaller, type StudyReportRequest } from "../src/services/studyReportBridge.js";
import type { SpawnedJson } from "../src/services/moduleCli.js";

/**
 * A study's report and the week's digest are open routes: the digest carries words and counts only,
 * and the orchestrator itself refuses a licensed study's report while the results gate is closed
 * (`cli.cmd_study_report`, tested in packages/orchestrator). Each checks its argument's shape before
 * anything spawns. Every figure here is invented.
 *
 * Shown to fail: with `isStudySlug` dropped, `..%2Fx` reached the bridge.
 */

function features(extra: Record<string, boolean> | null): BridgeResult {
  if (extra === null) return { ok: false, code: "unavailable", error: "config bridge unavailable" } as BridgeResult;
  return { ok: true, capabilities: {}, modules: {}, services: {}, features: { advisor: true, ...extra } } as unknown as BridgeResult;
}

const REPORT = {
  ok: true,
  study: "item7",
  provisional: true,
  windows: ["design", "holdout"],
  arms: [
    {
      arm: "wide",
      group: "arms",
      window: "design",
      file: "study-design.json",
      mean: 12,
      t: 2.1,
      n: 40,
      p: null,
      verdict: "fail",
      sessions: 100,
      entry_rate: 0.4,
      net_per_trade: 30,
      net_per_trade_derived: true,
      per_risk: 0.02,
      per_risk_base: 0.03,
      paired: { mean: 2, t: 0.5, n: 100, p: 0.3 },
      paired_vs: "base",
      paired_both: { mean: 20, t: 3, n: 40, p: 0.001 },
    },
    { arm: "broken" },
  ],
  comparisons: [],
  verdicts: [{ arm: "wide", verdict: "fail", why: null, file: "study-design.json" }],
  measures: [
    { key: "paired", label: "Gain over baseline", note: "n", t: "paired" },
    { key: "made_up", label: "x", note: "", t: null },
  ],
  gates: [{ key: "stabilization", name: "Stabilization gate", text: "waits" }],
  reading: [{ kind: "trades_less", text: "**wide** trades better but trades less." }],
  advice: [
    { kind: "new_study", text: "Declare a fallback study.", why: "it trades too rarely" },
    { kind: "buy_now", text: "never shown", why: "" },
  ],
  failed_steps: [],
};

const DIGEST = {
  ok: true,
  since: "2026-11-02",
  studies: [
    { id: 7, study: "item7", title: "t", status: "declared", outcome: null, finished: "2026-11-06T10:00:00", tally: { pass: 1, fail: 2, no_verdict: 0 }, provisional: true, failed_steps: [] },
    { id: 8, title: "no slug" },
  ],
  totals: { pass: 1, fail: 2, no_verdict: 0 },
  no_reading: [],
};

let spawned: StudyReportRequest[] = [];
function stub(reply: (req: StudyReportRequest) => SpawnedJson) {
  setStudyReportCaller(async (req) => {
    spawned.push(req);
    return reply(req);
  });
}

let app: FastifyInstance;
beforeAll(async () => {
  app = Fastify();
  registerBacktestRoutes(app);
  await app.ready();
});
afterAll(async () => {
  await app.close();
});
afterEach(() => {
  setBridgeCaller();
  setStudyReportCaller();
  invalidateFeatures();
  resetStudyReportCache();
  spawned = [];
});

describe("study report routes", () => {
  for (const [name, f] of [
    ["features failed", features(null)],
    ["the key is absent", features({})],
    ["the feature is false", features({ backtest_results: false })],
  ] as const) {
    it(`asks the orchestrator, which decides, when ${name}, and passes its withheld refusal on`, async () => {
      setBridgeCaller(() => f);
      stub(() => ({ ok: true, json: { ok: false, error: "withheld: this study's figures come from the vendor's licensed data" }, error: null }));
      const a = await app.inject({ method: "GET", url: "/api/backtest/studies/item7/report" });
      expect(a.statusCode).toBe(200);
      expect(a.json()).toMatchObject({ ok: false, data: null });
      expect(a.json().error).toContain("withheld");
      expect(a.body).not.toContain("wide");
      expect(spawned).toHaveLength(1);
    });
  }

  it("refuses a bad slug or date with a 400 and spawns nothing", async () => {
    setBridgeCaller(() => features({ backtest_results: true }));
    stub(() => ({ ok: true, json: REPORT, error: null }));
    for (const url of [
      "/api/backtest/studies/Item7/report",
      "/api/backtest/studies/..%2Fx/report",
      "/api/backtest/studies/item7%20--since/report",
      "/api/backtest/studies/digest",
      "/api/backtest/studies/digest?since=2026-13-45",
      "/api/backtest/studies/digest?since=yesterday",
    ]) {
      const r = await app.inject({ method: "GET", url });
      expect(r.statusCode, url).toBe(400);
    }
    expect(spawned).toEqual([]);
  });

  it("serves the report renamed, dropping a bad arm, an unknown measure and unknown advice", async () => {
    setBridgeCaller(() => features({ backtest_results: true }));
    stub(() => ({ ok: true, json: REPORT, error: null }));
    const r = await app.inject({ method: "GET", url: "/api/backtest/studies/item7/report" });
    expect(r.statusCode).toBe(200);
    const d = r.json().data;
    expect(spawned).toEqual([{ study: "item7" }]);
    expect(d.arms).toHaveLength(1);
    expect(d.arms[0]).toMatchObject({ arm: "wide", entryRate: 0.4, netPerTradeDerived: true, perRiskBase: 0.03, pairedVs: "base" });
    expect(d.arms[0].pairedBoth).toEqual({ mean: 20, t: 3, n: 40, p: 0.001 });
    expect(d.measures.map((m: { key: string }) => m.key)).toEqual(["paired"]);
    expect(d.advice).toEqual([{ kind: "new_study", text: "Declare a fallback study.", why: "it trades too rarely" }]);
    expect(d.reading[0].text).toContain("**wide**");
    // Memoised per study: a second read spawns nothing more.
    await app.inject({ method: "GET", url: "/api/backtest/studies/item7/report" });
    expect(spawned).toHaveLength(1);
  });

  it("serves the digest renamed and passes an orchestrator refusal through", async () => {
    setBridgeCaller(() => features({ backtest_results: true }));
    stub((req) => ("since" in req ? { ok: true, json: DIGEST, error: null } : { ok: true, json: { ok: false, error: "no register row for study item9" }, error: null }));
    const d = (await app.inject({ method: "GET", url: "/api/backtest/studies/digest?since=2026-11-02" })).json().data;
    expect(d.studies).toHaveLength(1);
    expect(d.studies[0].tally).toEqual({ pass: 1, fail: 2, noVerdict: 0 });
    expect(d.totals.noVerdict).toBe(0);
    const missing = (await app.inject({ method: "GET", url: "/api/backtest/studies/item9/report" })).json();
    expect(missing).toEqual({ ok: false, data: null, error: "no register row for study item9" });
  });
});
