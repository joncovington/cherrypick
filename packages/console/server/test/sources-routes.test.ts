import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import Fastify, { type FastifyInstance } from "fastify";
import { parseCoverage, registerSourcesRoutes } from "../src/routes/sources.js";
import { coverageArgv, resetSourcesCache, setSourcesCaller, type SourcesRequestKind } from "../src/services/sourcesBridge.js";
import { setBridgeCaller, type BridgeResult } from "../src/services/configBridge.js";
import { invalidateFeatures } from "../src/services/featuresBridge.js";
import type { SpawnedJson } from "../src/services/moduleCli.js";

/**
 * Backtest → sources is open to every reader, but the vendor's sources are licensed: the list leaves
 * them out unless their results may be shown (`backtest_results`), and coverage refuses to look at
 * them unless new work may use the vendor (`backtest`).
 *
 * Shown to fail: with the list's filter removed (`open || true`) the "gate off" list served the vendor
 * row; with the coverage check removed the licensed request spawned instead of a 403.
 */

function features(results: boolean | null, work = false): BridgeResult {
  if (results === null) return { ok: false, code: "unavailable", error: "config bridge unavailable" } as BridgeResult;
  return {
    ok: true,
    capabilities: {},
    modules: {},
    services: {},
    features: { advisor: true, backtest: work, backtest_results: results },
  } as unknown as BridgeResult;
}

const ROW = (name: string, licensed: boolean) => ({
  name,
  holds: "x",
  kind: licensed ? "chain" : "bars",
  fields: ["closes"],
  cadence: "1d",
  expiries: [],
  licensed,
  writer: "w",
  ongoing: true,
  rolling: false,
  note: null,
  state: "read",
  reason: null,
  symbols: 3,
  first: "2011-01-03",
  last: "2026-11-13",
  read_at: "2026-11-15T09:00:00",
});

const COVERAGE = {
  field: "closes",
  symbol: "SPY",
  start: "2019-01-02",
  end: "2026-11-13",
  cadence: null,
  expiry: null,
  covered: true,
  best: "stock-history",
  sources: [{ source: "stock-history", cadence: "1d", licensed: false, rolling: false, covers: true, why: null }],
};

let spawned: SourcesRequestKind[] = [];
let app: FastifyInstance;

function stub(reply: (r: SourcesRequestKind) => SpawnedJson) {
  setSourcesCaller(async (r) => {
    spawned.push(r);
    return reply(r);
  });
}

function gate(results: boolean | null, work = false) {
  invalidateFeatures();
  setBridgeCaller(async () => features(results, work));
}

beforeAll(async () => {
  app = Fastify();
  registerSourcesRoutes(app);
  await app.ready();
});

afterAll(async () => {
  await app.close();
});

afterEach(() => {
  spawned = [];
  resetSourcesCache();
  setSourcesCaller();
  setBridgeCaller();
  invalidateFeatures();
});

function listing(): SpawnedJson {
  return {
    ok: true,
    json: { generated_at: "2026-11-15T09:00:00", sources: [ROW("stock-history", false), ROW("vendor-oi", true)], undeclared: ["x.db"] },
    error: null,
  } as SpawnedJson;
}

describe("GET /api/sources", () => {
  it.each([
    ["off", false],
    ["unreadable", null],
  ] as const)("withholds the vendor's sources while their results are %s, and counts them", async (_label, results) => {
    gate(results);
    stub(listing);
    const res = await app.inject({ method: "GET", url: "/api/sources" });
    expect(res.statusCode).toBe(200);
    const body = res.json();
    expect(body.data.sources.map((s: { name: string }) => s.name)).toEqual(["stock-history"]);
    expect(body.data.withheld).toBe(1);
    expect(body.data.undeclared).toEqual(["x.db"]);
    expect(body.data.generated_at).toBe("2026-11-15T09:00:00");
  });

  it("lists every source while their results may be shown", async () => {
    gate(true);
    stub(listing);
    const body = (await app.inject({ method: "GET", url: "/api/sources" })).json();
    expect(body.data.sources).toHaveLength(2);
    expect(body.data.withheld).toBe(0);
    expect(spawned).toEqual([{ kind: "list" }]);
  });

  it("passes a failed read on as its error", async () => {
    gate(true);
    stub(() => ({ ok: false, json: null, error: "boom" }) as SpawnedJson);
    expect((await app.inject({ method: "GET", url: "/api/sources" })).json()).toEqual({ ok: false, data: null, error: "boom" });
  });
});

describe("GET /api/sources/coverage", () => {
  const q = "field=closes&symbol=spy&from=2019-01-02&to=2026-11-13";

  it("asks core with the checked values, the symbol upper-cased", async () => {
    gate(false);
    stub(() => ({ ok: true, json: COVERAGE, error: null }) as SpawnedJson);
    const res = await app.inject({ method: "GET", url: `/api/sources/coverage?${q}&cadence=1d` });
    expect(res.statusCode).toBe(200);
    expect(res.json().data.best).toBe("stock-history");
    expect(spawned).toEqual([
      { kind: "coverage", req: { field: "closes", symbol: "SPY", start: "2019-01-02", end: "2026-11-13", cadence: "1d", licensed: false } },
    ]);
  });

  it.each([
    ["field=price&symbol=SPY&from=2019-01-02&to=2019-02-01", "field must be one of"],
    ["field=closes&symbol=SP%20Y&from=2019-01-02&to=2019-02-01", "symbol is"],
    ["field=closes&symbol=SPY&from=2019-1-2&to=2019-02-01", "dates"],
    ["field=closes&symbol=SPY&from=2019-02-02&to=2019-02-01", "before"],
    ["field=closes&symbol=SPY&from=2019-01-02&to=2019-02-01&cadence=2m", "cadence must be one of"],
    ["field=chain&symbol=SPY&from=2019-01-02&to=2019-02-01&expiry=quarterly", "expiry must be one of"],
  ])("refuses %s before anything spawns", async (query, why) => {
    gate(true, true);
    stub(() => ({ ok: true, json: COVERAGE, error: null }) as SpawnedJson);
    const res = await app.inject({ method: "GET", url: `/api/sources/coverage?${query}` });
    expect(res.statusCode).toBe(400);
    expect(res.json().error).toContain(why);
    expect(spawned).toEqual([]);
  });

  it("refuses the vendor's sources unless new work may use the vendor", async () => {
    gate(true, false);
    stub(() => ({ ok: true, json: COVERAGE, error: null }) as SpawnedJson);
    const res = await app.inject({ method: "GET", url: `/api/sources/coverage?${q}&licensed=1` });
    expect(res.statusCode).toBe(403);
    expect(spawned).toEqual([]);
    gate(true, true);
    const ok = await app.inject({ method: "GET", url: `/api/sources/coverage?${q}&licensed=1` });
    expect(ok.statusCode).toBe(200);
    expect(spawned).toHaveLength(1);
  });
});

describe("the coverage argv", () => {
  it("passes only what was asked", () => {
    const req = parseCoverage({ field: "chain", symbol: "/es", from: "2026-11-05", to: "2026-11-13", expiry: "0dte", licensed: "1" });
    expect(typeof req).toBe("object");
    expect(coverageArgv(req as Exclude<typeof req, string>)).toEqual([
      "-m", "cherrypick.core.sources", "coverage", "--field", "chain", "--symbol", "/ES",
      "--from", "2026-11-05", "--to", "2026-11-13", "--expiry", "0dte", "--licensed",
    ]);
  });
});
