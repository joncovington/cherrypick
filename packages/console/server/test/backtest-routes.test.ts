import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import Fastify, { type FastifyInstance } from "fastify";
import { registerBacktestRoutes } from "../src/routes/backtest.js";
import { resetBacktestCache, setBacktestCaller, type BacktestRequest } from "../src/services/backtestBridge.js";
import { setBridgeCaller, type BridgeResult } from "../src/services/configBridge.js";
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
  invalidateFeatures();
  resetBacktestCache();
  spawned = [];
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
      expect(a.statusCode).toBe(404);
      expect(b.statusCode).toBe(404);
      // The unknown-API body, so a closed gate reads as no route at all.
      expect(a.json()).toEqual({ error: "not found" });
      expect(a.body).not.toContain("SPX");
      expect(spawned).toEqual([]);
    });
  }

  it("a bad run id is a 404 too while closed: the gate is checked first", async () => {
    setBridgeCaller(() => features({ backtest: false }));
    const r = await app.inject({ method: "GET", url: "/api/backtest/runs/..%2F..%2Fetc" });
    expect(r.statusCode).toBe(404);
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

  it("refuses a malformed run id with 400 and spawns nothing", async () => {
    setBridgeCaller(() => features({ backtest: true }));
    stubBacktest(() => ({ ok: true, json: OVERVIEW, error: null }));
    for (const bad of ["..%2F..%2Fetc", "20261017-093000-A1B2C3", "20261017-093000-a1b2c3x", "x20261017-093000-a1b2c3", "latest"]) {
      const r = await app.inject({ method: "GET", url: `/api/backtest/runs/${bad}` });
      expect(r.statusCode, bad).toBe(400);
    }
    expect(spawned).toEqual([]);
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
