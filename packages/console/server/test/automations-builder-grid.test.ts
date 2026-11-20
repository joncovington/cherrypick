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
 * The settings grid routes (#1294): "Try settings" sends a name and nothing else (the run and the split
 * are the backtest package's), and a licensed grid is shown, declared from or tested only while licensed
 * results may be shown. A withheld one is never spent.
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

const ID = "grid-mine-2611181000";
const SETTING = "profit_target_pct=50";
const GRID = (licensed: boolean) => ({ ok: true, name: ID, licensed, rows: [], tried: 14 }) as BridgeResult;

const post = (url: string, payload: unknown) =>
  app.inject({ method: "POST", url, payload, headers: { host: "127.0.0.1:5070", "x-csrf-token": CSRF_TOKEN } });
const get = (url: string) => app.inject({ method: "GET", url, headers: { host: "127.0.0.1:5070" } });

beforeAll(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-builder-grid-"));
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

describe("Try settings", () => {
  it("sends the name alone, whatever else the page sends", async () => {
    fakeBridge({ automation_grid: { ok: true, grid: ID, task: 513, run: "builder-mine-2611161730", split: "2026-07-18" } });
    const res = await post("/api/automations/builder/grid", { name: "mine", split: "2020-01-01", settings: ["x"] });
    expect(res.statusCode).toBe(200);
    expect(seen).toEqual([{ op: "automation_grid", name: "mine" }]);
    expect(res.json()).toMatchObject({ ok: true, grid: ID, task: 513, split: "2026-07-18" });
  });

  it("lists a licensed grid only while licensed results may be shown", async () => {
    const list = { ok: true, tasks: [], grids: [{ name: ID, licensed: true }, { name: "grid-mine-2611171000", licensed: false }] } as BridgeResult;
    fakeBridge({ automation_grids: list, features: features(false) });
    const shut = (await get("/api/automations/builder/grids?name=mine")).json();
    expect(shut.grids.map((g: { name: string }) => g.name)).toEqual(["grid-mine-2611171000"]);
    expect(shut.withheld).toBe(1);
  });
});

describe("Declare and the held-out test of a setting", () => {
  it("are asked for by name, grid and setting once the grid is readable", async () => {
    fakeBridge({ automation_grid_result: GRID(false), automation_grid_declare: { ok: true, setting: SETTING }, features: features(false) });
    const res = await post("/api/automations/builder/grid-declare", { name: "mine", id: ID, setting: SETTING });
    expect(res.statusCode).toBe(200);
    expect(seen.filter((r) => r.op !== "features")).toEqual([
      { op: "automation_grid_result", name: "mine", id: ID },
      { op: "automation_grid_declare", name: "mine", id: ID, setting: SETTING },
    ]);
  });

  it("never spends a withheld licensed grid's held-out part", async () => {
    fakeBridge({ automation_grid_result: GRID(true), automation_grid_holdout: { ok: true }, features: features(false) });
    const res = await post("/api/automations/builder/grid-holdout", { name: "mine", id: ID, setting: SETTING });
    expect(res.statusCode).toBe(403);
    expect(seen.map((r) => r.op)).not.toContain("automation_grid_holdout");
  });

  it("refuses an id or a setting shaped otherwise without asking", async () => {
    fakeBridge({});
    for (const body of [
      { name: "mine", id: "ideas-mine-2611181000", setting: SETTING },
      { name: "mine", id: ID, setting: "width=x1.5; rm" },
      { name: "mine", id: ID },
    ]) {
      expect((await post("/api/automations/builder/grid-holdout", body)).statusCode).toBe(400);
    }
    expect(seen).toEqual([]);
  });
});
