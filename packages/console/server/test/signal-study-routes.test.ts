import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import Fastify, { type FastifyInstance } from "fastify";
import { registerSignalStudyRoutes } from "../src/routes/signals.js";
import { resetSignalStudyCache, setSignalStudyCaller, type SignalStudyRequestKind } from "../src/services/signalStudyBridge.js";
import { setBridgeCaller, type BridgeResult } from "../src/services/configBridge.js";
import { invalidateFeatures } from "../src/services/featuresBridge.js";
import type { SpawnedJson } from "../src/services/moduleCli.js";

/**
 * Compare → signals is open to every reader, but a study over the vendor's store is licensed: the
 * list leaves it out and the one-result route 404s unless the features answer says `backtest: true`.
 *
 * Shown to fail: with the list's filter removed (`open || true`) the "gate off" list served the
 * licensed study, and with the one-result check removed its 404 became a 200.
 */

function features(backtest: boolean | null): BridgeResult {
  if (backtest === null) return { ok: false, code: "unavailable", error: "config bridge unavailable" } as BridgeResult;
  return { ok: true, capabilities: {}, modules: {}, services: {}, features: { advisor: true, backtest } } as unknown as BridgeResult;
}

const BRIEF = (name: string, licensed: boolean) => ({
  name,
  symbol: "SPY",
  source: licensed ? "vendor" : "history",
  kind: "gap",
  bar_minutes: null,
  first: "2011-01-03",
  last: "2026-11-13",
  firings: 3,
  licensed,
  generated_at: "2026-11-15T10:00:00-05:00",
});

const RESULT = (name: string, licensed: boolean) => ({
  ok: true,
  name,
  licensed,
  request: { name, signal: { kind: "gap" }, symbol: "SPY", source: "history", horizons: [1], distances: [] },
  em_index: "VIX",
  bars: { count: 3, first: "2011-01-03", last: "2011-01-05", sessions: 3 },
  dropped_sessions: { not_trading_day: 0, incomplete: 0 },
  firings_n: 1,
  firing_sessions: 1,
  control_n: 1,
  horizons: [],
  by_year: [],
  firings: [],
});

let spawned: SignalStudyRequestKind[] = [];
let app: FastifyInstance;

function stub(reply: (req: SignalStudyRequestKind) => SpawnedJson) {
  setSignalStudyCaller(async (req) => {
    spawned.push(req);
    return reply(req);
  });
}

function gate(backtest: boolean | null) {
  invalidateFeatures();
  setBridgeCaller(async () => features(backtest));
}

beforeAll(async () => {
  app = Fastify();
  registerSignalStudyRoutes(app);
  await app.ready();
});

afterAll(async () => {
  await app.close();
});

afterEach(() => {
  spawned = [];
  resetSignalStudyCache();
  setSignalStudyCaller();
  setBridgeCaller();
  invalidateFeatures();
});

function listing(): SpawnedJson {
  return { ok: true, json: { ok: true, studies: [BRIEF("public", false), BRIEF("vendor", true)], unreadable: [] }, error: null } as SpawnedJson;
}

describe("GET /api/signals", () => {
  it.each([
    ["off", false],
    ["unreadable", null],
  ] as const)("withholds a licensed study while the gate is %s, and counts it", async (_label, backtest) => {
    gate(backtest);
    stub(listing);
    const res = await app.inject({ method: "GET", url: "/api/signals" });
    expect(res.statusCode).toBe(200);
    const body = res.json();
    expect(body.ok).toBe(true);
    expect(body.data.studies.map((s: { name: string }) => s.name)).toEqual(["public"]);
    expect(body.data.withheld).toBe(1);
  });

  it("lists every study while the gate is on", async () => {
    gate(true);
    stub(listing);
    const body = (await app.inject({ method: "GET", url: "/api/signals" })).json();
    expect(body.data.studies).toHaveLength(2);
    expect(body.data.withheld).toBe(0);
  });

  it("passes a failed read on as its error", async () => {
    gate(true);
    stub(() => ({ ok: false, json: null, error: "boom" }) as SpawnedJson);
    const body = (await app.inject({ method: "GET", url: "/api/signals" })).json();
    expect(body).toEqual({ ok: false, data: null, error: "boom" });
  });
});

describe("GET /api/signals/:name", () => {
  it("serves a public study with the gate off", async () => {
    gate(false);
    stub(() => ({ ok: true, json: RESULT("public", false), error: null }) as SpawnedJson);
    const res = await app.inject({ method: "GET", url: "/api/signals/public" });
    expect(res.statusCode).toBe(200);
    expect(res.json().data.name).toBe("public");
    expect(spawned).toEqual([{ kind: "one", name: "public" }]);
  });

  it.each([
    ["off", false],
    ["unreadable", null],
  ] as const)("answers a licensed study with the unknown-route 404 while the gate is %s", async (_label, backtest) => {
    gate(backtest);
    stub(() => ({ ok: true, json: RESULT("vendor", true), error: null }) as SpawnedJson);
    const res = await app.inject({ method: "GET", url: "/api/signals/vendor" });
    expect(res.statusCode).toBe(404);
    expect(res.json()).toEqual({ error: "not found" });
  });

  it("serves a licensed study while the gate is on", async () => {
    gate(true);
    stub(() => ({ ok: true, json: RESULT("vendor", true), error: null }) as SpawnedJson);
    expect((await app.inject({ method: "GET", url: "/api/signals/vendor" })).statusCode).toBe(200);
  });

  it("refuses a name the module would not write, before anything spawns", async () => {
    gate(true);
    stub(() => ({ ok: true, json: RESULT("x", false), error: null }) as SpawnedJson);
    for (const bad of ["Bad", "..%2Fx", "a b", "-lead"]) {
      expect((await app.inject({ method: "GET", url: `/api/signals/${bad}` })).statusCode).toBe(400);
    }
    expect(spawned).toEqual([]);
  });

  it("passes the module's refusal on as its error", async () => {
    gate(false);
    stub(() => ({ ok: true, json: { ok: false, error: "no signal study named absent" }, error: null }) as SpawnedJson);
    const body = (await app.inject({ method: "GET", url: "/api/signals/absent" })).json();
    expect(body).toEqual({ ok: false, data: null, error: "no signal study named absent" });
  });
});
