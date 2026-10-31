import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import Fastify, { type FastifyInstance } from "fastify";
import { registerBacktestRoutes } from "../src/routes/backtest.js";
import { resetBacktestCache, setBacktestCaller, type BacktestRequest } from "../src/services/backtestBridge.js";
import { setBridgeCaller, type BridgeResult } from "../src/services/configBridge.js";
import { resetResearchQueueCache, setResearchQueueCaller } from "../src/services/researchQueueBridge.js";
import { resetStudiesCache, setStudiesCaller } from "../src/services/studiesBridge.js";
import { backtestOn, invalidateFeatures } from "../src/services/featuresBridge.js";
import type { SpawnedJson } from "../src/services/moduleCli.js";

/**
 * The Backtest page shows results computed from licensed vendor history, so its routes FAIL CLOSED:
 * each refuses with the unknown-route 404 unless the orchestrator's features answer was read and
 * says `backtest: true`. The browser hiding the page is never what keeps the data in. The features
 * answer is driven through the real config-bridge seam, so the gate under test is the one the
 * server runs.
 *
 * Shown to fail: with `backtestOn` loosened to fail open (`!features.ok || ... !== false`), the
 * "features failed" and "key absent" cases served 200 and spawned the bridge.
 */

const RUN_ID = "20261017-093000-a1b2c3";

const OVERVIEW = {
  ok: true,
  license: { active: true, terminated_on: null, purge_by: null, days_left: null, overdue: false },
  windows: { design: ["2023-01-03", "2025-06-30"], holdout: ["2025-07-01", "2026-06-30"] },
  stores: [{ symbol: "SPX", cadence: 60, file: "SPX-60s.db", complete: 3, span: ["2026-10-13", "2026-10-15"], refused: 0, refused_reasons: {} }],
  validation: [],
  runs: [],
  trials: { flies: 1 },
};

function features(extra: Record<string, boolean> | null): BridgeResult {
  if (extra === null) return { ok: false, code: "unavailable", error: "config bridge unavailable" } as BridgeResult;
  return {
    ok: true,
    capabilities: {},
    modules: {},
    services: {},
    features: { advisor: true, ...extra },
  } as unknown as BridgeResult;
}

let spawned: BacktestRequest[] = [];
function stubBacktest(reply: (req: BacktestRequest) => SpawnedJson) {
  setBacktestCaller(async (req) => {
    spawned.push(req);
    return reply(req);
  });
}

const QUEUE = {
  ok: true,
  session_held: true,
  blackout: ["18:30", "20:30"],
  tasks: [
    {
      id: 2,
      study: "item17",
      step: "fetch-chains",
      kind: "fetch-chains",
      output: null,
      result: { log: "C:/logs/2.log", report: { complete: 40, refused: 1, stopped: true }, input: "C:/x/item17/requests.json", store: "backtest eod_chains.db" },
      args: { requests: "C:/x/item17/requests.json", workers: 3 },
      needs: "thetadata",
      not_before: null,
      stop_by: null,
      status: "queued",
      created_at: "2026-10-24T20:00:00",
      started_at: null,
      finished_at: null,
      runs: 0,
      pid: null,
      exit_code: null,
      note: null,
      waits: "the ThetaData session is held by another process",
    },
    { id: 1, study: "x", step: "y", kind: "shell", status: "nonsense" }, // an unknown status is dropped, never shown as one it is not
    { id: 0, kind: "fetch-chains", status: "done" }, // a row without its name is dropped too
  ],
};

let queueSpawns = 0;
function stubQueue(reply: SpawnedJson) {
  setResearchQueueCaller(async () => {
    queueSpawns += 1;
    return reply;
  });
}

const STUDIES = {
  ok: true,
  unregistered: ["item22"],
  rows: [
    {
      id: 23,
      study: "item23",
      title: "The short delta",
      status: "failed",
      legacy: false,
      pointer: "docs/edge-hypotheses-plan.md#23-the-short-delta",
      outcome: "live stays at 16 delta",
      rule: "t > 2",
      rivals: [{ explanation: "a calm span", check: "both halves" }, { explanation: "no check" }],
      group: "finished",
      results: [
        {
          path: "{home}/data/backtest/results/research/item23/study-design.json",
          name: "study-design.json",
          present: true,
          modified: "2026-10-28T06:50:12",
          window: "design",
          span: null,
          run_id: "20261028-065012-0174bd",
          provisional: true,
          verdicts: [{ arm: "d20", verdict: "fail" }],
          summary: "fail",
          grid: {
            parameter: "short delta",
            measure: "mean net per session",
            declared: "d16",
            rows: [
              { arm: "d16", setting: 0.16, label: "0.16", mean: 5, n: 120, verdict: null, declared: true },
              { arm: "d20", setting: 0.2, label: "0.2", mean: 3, n: 120, verdict: "fail", declared: false },
              { arm: "bad", label: "no setting" },
            ],
            neighbourhood: { d16: "plateau", d20: 7 },
          },
          notes: "| arm | win % |",
        },
      ],
      queue: { active: false, tasks: [{ id: 24, step: "holdout", status: "done", finished_at: "2026-10-28T23:38:59" }] },
      last_activity: "2026-10-28T23:38:59",
    },
    { id: 99, study: "item99", title: "no group" },
  ],
};

let studiesSpawns = 0;
function stubStudies(reply: SpawnedJson) {
  setStudiesCaller(async () => {
    studiesSpawns += 1;
    return reply;
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
  setBacktestCaller();
  setResearchQueueCaller();
  setStudiesCaller();
  invalidateFeatures();
  resetBacktestCache();
  resetResearchQueueCache();
  resetStudiesCache();
  spawned = [];
  queueSpawns = 0;
  studiesSpawns = 0;
});

const closed: Array<[string, BridgeResult]> = [
  ["features failed", features(null)],
  ["the key is absent", features({})],
  ["the feature is false", features({ backtest: false })],
];

describe("the gate is closed", () => {
  for (const [name, f] of closed) {
    it(`both routes 404 when ${name}, and nothing is spawned`, async () => {
      setBridgeCaller(() => f);
      stubBacktest(() => ({ ok: true, json: OVERVIEW, error: null }));
      const a = await app.inject({ method: "GET", url: "/api/backtest/overview" });
      const b = await app.inject({ method: "GET", url: `/api/backtest/runs/${RUN_ID}` });
      stubQueue({ ok: true, json: QUEUE, error: null });
      const c = await app.inject({ method: "GET", url: "/api/backtest/queue" });
      stubStudies({ ok: true, json: STUDIES, error: null });
      const d = await app.inject({ method: "GET", url: "/api/backtest/studies" });
      expect(a.statusCode).toBe(404);
      expect(b.statusCode).toBe(404);
      expect(c.statusCode).toBe(404);
      expect(d.statusCode).toBe(404);
      expect(d.body).not.toContain("item23");
      expect(studiesSpawns).toBe(0);
      // The unknown-API body, so a closed gate reads as no route at all.
      expect(a.json()).toEqual({ error: "not found" });
      expect(a.body).not.toContain("SPX");
      expect(c.body).not.toContain("item17");
      expect(spawned).toEqual([]);
      expect(queueSpawns).toBe(0);
    });
  }

  it("a bad run id is a 404 too while closed: the gate is checked first", async () => {
    setBridgeCaller(() => features({ backtest: false }));
    const r = await app.inject({ method: "GET", url: "/api/backtest/runs/..%2F..%2Fetc" });
    expect(r.statusCode).toBe(404);
  });

  it("the trade log is a 404 while closed, whatever the arm, and nothing is spawned", async () => {
    setBridgeCaller(() => features({ backtest: false }));
    stubBacktest(() => ({ ok: true, json: { ok: true, trades: [] }, error: null }));
    for (const arm of ["pm-200", "../x"]) {
      const r = await app.inject({ method: "GET", url: `/api/backtest/runs/${RUN_ID}/trades?arm=${encodeURIComponent(arm)}` });
      expect(r.statusCode, arm).toBe(404);
    }
    expect(spawned).toEqual([]);
  });
});

describe("the gate is open", () => {
  it("serves the overview through the bridge", async () => {
    setBridgeCaller(() => features({ backtest: true }));
    stubBacktest(() => ({ ok: true, json: OVERVIEW, error: null }));
    const r = await app.inject({ method: "GET", url: "/api/backtest/overview" });
    expect(r.statusCode).toBe(200);
    const body = r.json();
    expect(body.ok).toBe(true);
    expect(body.data.stores[0].file).toBe("SPX-60s.db");
    expect(spawned).toEqual([{ kind: "overview" }]);
  });

  it("serves a run's report, and passes the module's refusal on as an error", async () => {
    setBridgeCaller(() => features({ backtest: true }));
    stubBacktest((req) =>
      req.kind === "run" && req.id === RUN_ID
        ? { ok: true, json: { ok: true, report: { run_id: RUN_ID, arms: {} } }, error: null }
        : { ok: true, json: { ok: false, error: "no run" }, error: null },
    );
    const r = await app.inject({ method: "GET", url: `/api/backtest/runs/${RUN_ID}` });
    expect(r.statusCode).toBe(200);
    expect(r.json()).toEqual({ ok: true, data: { run_id: RUN_ID, arms: {} }, error: null });
    const missing = await app.inject({ method: "GET", url: "/api/backtest/runs/20261017-093000-ffffff" });
    expect(missing.json()).toEqual({ ok: false, data: null, error: "no run" });
  });

  it("attaches the run's cards to its report, and hands a chosen capital to the module", async () => {
    setBridgeCaller(() => features({ backtest: true }));
    const cards = { ok: true, account: { capital: 900, basis: "peak_risk", peak_risk: 900 }, arms: {} };
    stubBacktest(() => ({ ok: true, json: { ok: true, report: { run_id: RUN_ID, arms: {} }, cards }, error: null }));
    const r = await app.inject({ method: "GET", url: `/api/backtest/runs/${RUN_ID}` });
    expect(r.json().data).toEqual({ run_id: RUN_ID, arms: {}, cards });
    await app.inject({ method: "GET", url: `/api/backtest/runs/${RUN_ID}?capital=50000` });
    expect(spawned).toEqual([
      { kind: "run", id: RUN_ID },
      { kind: "run", id: RUN_ID, capital: 50000 },
    ]);
  });

  it("refuses a capital that is not whole dollars with 400 and spawns nothing", async () => {
    setBridgeCaller(() => features({ backtest: true }));
    stubBacktest(() => ({ ok: true, json: OVERVIEW, error: null }));
    for (const bad of ["0", "-5", "1e6", "50000.5", "0050", "abc", "", "--id", "12345678901"]) {
      const r = await app.inject({ method: "GET", url: `/api/backtest/runs/${RUN_ID}?capital=${encodeURIComponent(bad)}` });
      expect(r.statusCode, bad).toBe(400);
    }
    expect(spawned).toEqual([]);
  });

  it("refuses a malformed run id with 400 and spawns nothing", async () => {
    setBridgeCaller(() => features({ backtest: true }));
    stubBacktest(() => ({ ok: true, json: OVERVIEW, error: null }));
    for (const bad of ["..%2F..%2Fetc", "20261017-093000-A1B2C3", "20261017-093000-a1b2c3x", "x20261017-093000-a1b2c3", "latest"]) {
      const r = await app.inject({ method: "GET", url: `/api/backtest/runs/${bad}` });
      expect(r.statusCode, bad).toBe(400);
    }
    expect(spawned).toEqual([]);
  });

  it("serves one arm's trade log, and passes the module's refusal of an unknown arm on as an error", async () => {
    setBridgeCaller(() => features({ backtest: true }));
    const trade = { session: "2024-01-02", outcome: "per_side_stop", net_pnl: -50 };
    stubBacktest((req) =>
      req.kind === "trades" && req.arm === "pm-200"
        ? { ok: true, json: { ok: true, run_id: RUN_ID, arm: "pm-200", trades: [trade] }, error: null }
        : { ok: true, json: { ok: false, error: "run has no arm 'other'" }, error: null },
    );
    const r = await app.inject({ method: "GET", url: `/api/backtest/runs/${RUN_ID}/trades?arm=pm-200` });
    expect(r.json()).toEqual({ ok: true, data: { run_id: RUN_ID, arm: "pm-200", trades: [trade] }, error: null });
    const other = await app.inject({ method: "GET", url: `/api/backtest/runs/${RUN_ID}/trades?arm=other` });
    expect(other.json()).toEqual({ ok: false, data: null, error: "run has no arm 'other'" });
    expect(spawned).toEqual([
      { kind: "trades", id: RUN_ID, arm: "pm-200" },
      { kind: "trades", id: RUN_ID, arm: "other" },
    ]);
  });

  it("refuses a missing or malformed arm with 400 and spawns nothing", async () => {
    setBridgeCaller(() => features({ backtest: true }));
    stubBacktest(() => ({ ok: true, json: OVERVIEW, error: null }));
    for (const bad of ["", "--id", "../x", "a b", "x".repeat(81)]) {
      const r = await app.inject({ method: "GET", url: `/api/backtest/runs/${RUN_ID}/trades?arm=${encodeURIComponent(bad)}` });
      expect(r.statusCode, bad).toBe(400);
    }
    expect((await app.inject({ method: "GET", url: `/api/backtest/runs/${RUN_ID}/trades` })).statusCode).toBe(400);
    expect((await app.inject({ method: "GET", url: `/api/backtest/runs/latest/trades?arm=x` })).statusCode).toBe(400);
    expect(spawned).toEqual([]);
  });

  it("serves the research queue renamed, dropping a row it cannot read", async () => {
    setBridgeCaller(() => features({ backtest: true }));
    stubQueue({ ok: true, json: QUEUE, error: null });
    const r = await app.inject({ method: "GET", url: "/api/backtest/queue" });
    expect(r.statusCode).toBe(200);
    const body = r.json();
    expect(body.ok).toBe(true);
    expect(body.data.sessionHeld).toBe(true);
    expect(body.data.blackout).toEqual(["18:30", "20:30"]);
    expect(body.data.tasks).toHaveLength(1);
    expect(body.data.tasks[0]).toMatchObject({
      id: 2,
      study: "item17",
      step: "fetch-chains",
      name: "item17/fetch-chains",
      kind: "fetch-chains",
      needs: "thetadata",
      status: "queued",
      runs: 0,
      waits: "the ThetaData session is held by another process",
    });
    expect(body.data.tasks[0].result).toEqual({
      log: "C:/logs/2.log",
      report: { complete: 40, refused: 1, stopped: true },
      input: "C:/x/item17/requests.json",
      store: "backtest eod_chains.db",
      output: null,
      outputExists: null,
    });
    const failed = { ok: false, json: null, error: "research queue read unavailable — exit 2" };
    resetResearchQueueCache();
    stubQueue(failed);
    expect((await app.inject({ method: "GET", url: "/api/backtest/queue" })).json()).toEqual({
      ok: false,
      data: null,
      error: "research queue read unavailable — exit 2",
    });
  });

  it("serves the studies board renamed: a result keeps its file name only, a bad row is dropped", async () => {
    setBridgeCaller(() => features({ backtest: true }));
    stubStudies({ ok: true, json: STUDIES, error: null });
    const r = await app.inject({ method: "GET", url: "/api/backtest/studies" });
    expect(r.statusCode).toBe(200);
    const body = r.json();
    expect(body.ok).toBe(true);
    expect(body.data.unregistered).toEqual(["item22"]);
    expect(body.data.rows).toHaveLength(1);
    expect(body.data.rows[0]).toMatchObject({
      id: 23,
      study: "item23",
      status: "failed",
      group: "finished",
      outcome: "live stays at 16 delta",
      queueActive: false,
      lastActivity: "2026-10-28T23:38:59",
      rivals: [{ explanation: "a calm span", check: "both halves" }],
      tasks: [{ id: 24, step: "holdout", status: "done", createdAt: null, startedAt: null, finishedAt: "2026-10-28T23:38:59" }],
    });
    expect(body.data.rows[0].results[0]).toEqual({
      name: "study-design.json",
      present: true,
      modified: "2026-10-28T06:50:12",
      window: "design",
      span: null,
      runId: "20261028-065012-0174bd",
      provisional: true,
      verdicts: [{ arm: "d20", verdict: "fail" }],
      summary: "fail",
      // A row without a setting and a label that is not a word are dropped, not guessed.
      grid: {
        parameter: "short delta",
        measure: "mean net per session",
        declared: "d16",
        rows: [
          { arm: "d16", setting: 0.16, label: "0.16", mean: 5, n: 120, verdict: null, declared: true },
          { arm: "d20", setting: 0.2, label: "0.2", mean: 3, n: 120, verdict: "fail", declared: false },
        ],
        neighbourhood: { d16: "plateau" },
      },
      notes: "| arm | win % |",
    });
    expect(r.body).not.toContain("{home}");
    expect(studiesSpawns).toBe(1);
  });

  it("a failed subprocess is an error, never an empty overview", async () => {
    setBridgeCaller(() => features({ backtest: true }));
    stubBacktest(() => ({ ok: false, json: null, error: "backtest read unavailable — exit 2" }));
    const r = await app.inject({ method: "GET", url: "/api/backtest/overview" });
    expect(r.statusCode).toBe(200);
    expect(r.json()).toEqual({ ok: false, data: null, error: "backtest read unavailable — exit 2" });
  });
});

describe("backtestOn", () => {
  it("is true only for an explicit true", () => {
    expect(backtestOn(undefined)).toBe(false);
    expect(backtestOn({ ok: false, error: "x" })).toBe(false);
    const ok = { ok: true as const, capabilities: {}, modules: {}, services: {}, features: {} };
    expect(backtestOn(ok)).toBe(false);
    expect(backtestOn({ ...ok, features: { backtest: false } })).toBe(false);
    expect(backtestOn({ ...ok, features: { backtest: true } })).toBe(true);
  });
});
