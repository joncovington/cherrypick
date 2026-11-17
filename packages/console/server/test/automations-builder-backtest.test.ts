import { describe, it, expect, beforeAll, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Fastify, { type FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../src/config.js";
import { registerSecurity, CSRF_TOKEN } from "../src/security.js";
import { registerConfigRoutes } from "../src/routes/configOps.js";
import { setBridgeCaller, type BridgeRequest, type BridgeResult } from "../src/services/configBridge.js";
import { invalidateFeatures } from "../src/services/featuresBridge.js";

/**
 * The Builder's backtest routes (#1180): the button sends a name and nothing else (the window and the
 * source are the module's), and the run card shows a licensed result only while licensed results may
 * be shown.
 */

let app: FastifyInstance;
let seen: BridgeRequest[] = [];

function fakeBridge(answers: Partial<Record<BridgeRequest["op"], BridgeResult>>) {
  setBridgeCaller((req) => {
    seen.push(req);
    return answers[req.op] ?? { ok: false, code: "bad_request", error: `unexpected ${req.op}` };
  });
}

function features(results: boolean): BridgeResult {
  return { ok: true, modules: {}, features: { advisor: true, backtest: false, backtest_results: results } } as BridgeResult;
}

const RUN = (licensed: boolean, run: string) => ({
  run,
  symbol: "SPX",
  source: licensed ? "vendor-0dte-spx-60s" : "recorded-0dte",
  licensed,
  summary: { mine: { sessions: 20, trades: 18, net: 120.5, mean_net: 6.69 } },
});

const post = (url: string, payload: unknown) =>
  app.inject({ method: "POST", url, payload, headers: { host: "127.0.0.1:5070", "x-csrf-token": CSRF_TOKEN } });
const get = (url: string) => app.inject({ method: "GET", url, headers: { host: "127.0.0.1:5070" } });

beforeAll(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-builder-backtest-"));
  const config = { port: 0, paths: { cherrypick: tmp, orchestratorConfig: path.join(tmp, "config.json") } } as unknown as ConsoleConfig;
  app = Fastify();
  registerSecurity(app);
  registerConfigRoutes(app, config);
  await app.ready();
});

afterEach(() => {
  setBridgeCaller();
  invalidateFeatures();
  seen = [];
});

describe("the Builder's backtest button", () => {
  it("sends the name alone, whatever else the page sends", async () => {
    fakeBridge({ automation_backtest: { ok: true, run: "builder-mine-2611161730", task: 401, window: ["2025-11-14", "2026-11-13"], source: "recorded-0dte" } });
    const res = await post("/api/automations/builder/backtest", { name: "mine", window: ["2011-01-01", "2026-11-13"], source: "vendor-eod-chains" });
    expect(res.statusCode).toBe(200);
    expect(seen).toEqual([{ op: "automation_backtest", name: "mine" }]);
    expect(res.json()).toMatchObject({ ok: true, name: "mine", task: 401, source: "recorded-0dte" });
  });

  it("passes the orchestrator's refusal through", async () => {
    fakeBridge({ automation_backtest: { ok: false, code: "invalid", error: "our data does not cover a backtest of it (chain)" } });
    const res = await post("/api/automations/builder/backtest", { name: "mine" });
    expect(res.statusCode).toBeGreaterThanOrEqual(400);
    expect(res.json().error).toContain("does not cover");
  });

  it("refuses a body without a name, and needs the CSRF token", async () => {
    expect((await post("/api/automations/builder/backtest", {})).statusCode).toBe(400);
    const bare = await app.inject({ method: "POST", url: "/api/automations/builder/backtest", payload: { name: "mine" }, headers: { host: "127.0.0.1:5070" } });
    expect(bare.statusCode).toBe(403);
    expect(seen).toEqual([]);
  });
});

describe("the run card", () => {
  const runs: BridgeResult = {
    ok: true,
    name: "mine",
    tasks: [{ id: 401, run: "builder-mine-2611161730", status: "done" }],
    runs: [RUN(false, "builder-mine-2611161730"), RUN(true, "builder-mine-2611151730"), { ...RUN(false, "x"), licensed: undefined }],
    results_unavailable: false,
  } as BridgeResult;

  it("withholds licensed results, and any not plainly unlicensed, while the gate is shut", async () => {
    fakeBridge({ automation_runs: runs, features: features(false) });
    const res = await get("/api/automations/builder/runs?name=mine");
    expect(res.statusCode).toBe(200);
    const body = res.json();
    expect(body.runs.map((r: { run: string }) => r.run)).toEqual(["builder-mine-2611161730"]);
    expect(body.withheld).toBe(2);
    expect(body.tasks).toHaveLength(1);
  });

  it("shows them while it is open", async () => {
    fakeBridge({ automation_runs: runs, features: features(true) });
    const body = (await get("/api/automations/builder/runs?name=mine")).json();
    expect(body.runs).toHaveLength(3);
    expect(body.withheld).toBe(0);
  });

  it("refuses a missing name", async () => {
    expect((await get("/api/automations/builder/runs")).statusCode).toBe(400);
  });
});
