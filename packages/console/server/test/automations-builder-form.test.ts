import { describe, it, expect, beforeAll, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Fastify, { type FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../src/config.js";
import { registerSecurity, CSRF_TOKEN } from "../src/security.js";
import { registerConfigRoutes } from "../src/routes/configOps.js";
import { setBridgeCaller, type BridgeRequest, type BridgeResult } from "../src/services/configBridge.js";
import { readBuilderSchema, setBuilderSchemaCaller } from "../src/services/automationsBuilderBridge.js";

/**
 * The Builder form's two reads: the schema straight from the module, and "Today, live" through the
 * orchestrator, which carries an unsaved edit and nothing else, and writes nothing.
 */

let app: FastifyInstance;
let seen: BridgeRequest[] = [];

const post = (url: string, payload: unknown) =>
  app.inject({ method: "POST", url, payload, headers: { host: "127.0.0.1:5070", "x-csrf-token": CSRF_TOKEN } });

beforeAll(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-builder-form-"));
  const config = { port: 0, paths: { cherrypick: tmp, orchestratorConfig: path.join(tmp, "config.json") } } as unknown as ConsoleConfig;
  app = Fastify();
  registerSecurity(app);
  registerConfigRoutes(app, config);
  await app.ready();
});

afterEach(() => {
  setBridgeCaller();
  setBuilderSchemaCaller();
  seen = [];
});

describe("today, live", () => {
  it("sends the name and an unsaved edit, nothing else, and returns the module's answer", async () => {
    setBridgeCaller((req) => {
      seen.push(req);
      return { ok: true, name: "mine", state: "would_skip", reason: "gate:mid_price" } as BridgeResult;
    });
    const res = await post("/api/automations/builder/today", { name: "mine", automation: { symbol: "SPX" }, enabled: true });
    expect(res.statusCode).toBe(200);
    expect(seen).toEqual([{ op: "automation_today", name: "mine", automation: { symbol: "SPX" } }]);
    expect(res.json()).toEqual({ name: "mine", state: "would_skip", reason: "gate:mid_price" });
  });

  it("refuses a body without a name, and needs the CSRF token", async () => {
    expect((await post("/api/automations/builder/today", {})).statusCode).toBe(400);
    const bare = await app.inject({ method: "POST", url: "/api/automations/builder/today", payload: { name: "mine" }, headers: { host: "127.0.0.1:5070" } });
    expect(bare.statusCode).toBe(403);
    expect(seen).toEqual([]);
  });
});

describe("the payoff under the legs", () => {
  it("sends the name and an unsaved edit, nothing else, and returns the module's answer", async () => {
    setBridgeCaller((req) => {
      seen.push(req);
      return { ok: true, name: "mine", state: "placed", curve: [[1, -2], [3, 4]] } as BridgeResult;
    });
    const res = await post("/api/automations/builder/payoff", { name: "mine", automation: { symbol: "SPX" }, strikes: [1] });
    expect(res.statusCode).toBe(200);
    expect(seen).toEqual([{ op: "automation_payoff", name: "mine", automation: { symbol: "SPX" } }]);
    expect(res.json()).toEqual({ name: "mine", state: "placed", curve: [[1, -2], [3, 4]] });
  });

  it("refuses a body without a name, and needs the CSRF token", async () => {
    expect((await post("/api/automations/builder/payoff", {})).statusCode).toBe(400);
    const bare = await app.inject({ method: "POST", url: "/api/automations/builder/payoff", payload: { name: "mine" }, headers: { host: "127.0.0.1:5070" } });
    expect(bare.statusCode).toBe(403);
    expect(seen).toEqual([]);
  });
});

describe("the form's schema", () => {
  it("is the module's answer without its ok flag, and a failed read says so", async () => {
    setBuilderSchemaCaller(() => ({ ok: true, json: { ok: true, exits: ["touch"] }, error: null }));
    expect(await readBuilderSchema(1)).toEqual({ schema: { exits: ["touch"] }, error: null });
    setBuilderSchemaCaller(() => ({ ok: false, json: null, error: "not installed" }));
    expect(await readBuilderSchema(2)).toEqual({ schema: null, error: "not installed" });
  });
});
