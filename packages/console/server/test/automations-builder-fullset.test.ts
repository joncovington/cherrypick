import { describe, it, expect, beforeAll, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Fastify, { type FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../src/config.js";
import { registerSecurity, CSRF_TOKEN } from "../src/security.js";
import { registerConfigRoutes } from "../src/routes/configOps.js";
import { setBridgeCaller, type BridgeRequest, type BridgeResult } from "../src/services/configBridge.js";

/**
 * Adding an underlying to the full set from the Builder (#1253): the plan is the orchestrator's dry run,
 * and the addition sends the symbol and the automation's name only (the span, the cap and the rows are
 * the orchestrator's), behind the CSRF token like every write.
 */

let app: FastifyInstance;
let seen: BridgeRequest[] = [];

function fakeBridge(answers: Partial<Record<BridgeRequest["op"], BridgeResult>>) {
  setBridgeCaller((req) => {
    seen.push(req);
    return answers[req.op] ?? { ok: false, code: "bad_request", error: `unexpected ${req.op}` };
  });
}

const post = (url: string, payload: unknown, token = true) =>
  app.inject({
    method: "POST",
    url,
    payload,
    headers: { host: "127.0.0.1:5070", ...(token ? { "x-csrf-token": CSRF_TOKEN } : {}) },
  });
const get = (url: string) => app.inject({ method: "GET", url, headers: { host: "127.0.0.1:5070" } });

beforeAll(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-builder-fullset-"));
  const config = { port: 0, paths: { cherrypick: tmp, orchestratorConfig: path.join(tmp, "config.json") } } as unknown as ConsoleConfig;
  app = Fastify();
  registerSecurity(app);
  registerConfigRoutes(app, config);
  await app.ready();
});

afterEach(() => {
  setBridgeCaller();
  seen = [];
});

describe("adding an underlying to the full set", () => {
  it("plans by symbol alone and passes the planner's counts through", async () => {
    fakeBridge({
      fullset_plan: { ok: true, from: "2025-11-17", through: "2026-11-16", requests: { sessions: 250, "near_ABC.json": 1200 },
        concurrent: 2, cap_sessions: 300, within_cap: true } as BridgeResult,
    });
    const res = await get("/api/automations/builder/fullset-plan?symbol=abc");
    expect(res.statusCode).toBe(200);
    expect(seen).toEqual([{ op: "fullset_plan", symbol: "ABC" }]);
    expect(res.json()).toMatchObject({ symbol: "ABC", within_cap: true, requests: { sessions: 250 }, concurrent: 2 });
    expect((await get("/api/automations/builder/fullset-plan?symbol=A%20B")).statusCode).toBe(400);
  });

  it("adds with the symbol and the automation's name, and says whether its backtest was queued after", async () => {
    fakeBridge({
      fullset_add: { ok: true, from: "2025-11-17", through: "2026-11-16", tasks: [501, 502], run: { ok: true, task: 9, run: "builder-mine-1" } } as BridgeResult,
    });
    const res = await post("/api/automations/builder/fullset-add", { name: "mine", symbol: "abc", from: "2020-01-02", rows: [] });
    expect(res.statusCode).toBe(200);
    expect(seen).toEqual([{ op: "fullset_add", symbol: "ABC", run: "mine" }]);
    expect(res.json()).toMatchObject({ ok: true, symbol: "ABC", tasks: [501, 502], run: { task: 9 }, run_error: null });
  });

  it("passes a refusal through with its status, and refuses a write without the token", async () => {
    fakeBridge({ fullset_add: { ok: false, code: "unavailable", error: "new work may not use the vendor: vendor licence ended" } });
    const res = await post("/api/automations/builder/fullset-add", { name: "mine", symbol: "ABC" });
    expect(res.statusCode).toBeGreaterThanOrEqual(400);
    expect(JSON.stringify(res.json())).toContain("licence ended");
    seen = [];
    expect((await post("/api/automations/builder/fullset-add", { name: "mine", symbol: "ABC" }, false)).statusCode).toBe(403);
    expect(seen).toEqual([]);
  });
});
