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
 * The Builder's editor routes (#1180): the check and the save both go through the orchestrator's
 * config editor, the save carries only a name and an automation (never a switch), and a problem the
 * module names reaches the page as a list.
 */

let app: FastifyInstance;
let seen: BridgeRequest[] = [];

function fakeBridge(response: BridgeResult) {
  setBridgeCaller((req) => {
    seen.push(req);
    return response;
  });
}

const post = (url: string, payload: unknown, headers: Record<string, string> = {}) =>
  app.inject({ method: "POST", url, payload, headers: { host: "127.0.0.1:5070", "x-csrf-token": CSRF_TOKEN, ...headers } });

beforeAll(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-builder-save-"));
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

describe("the Builder's check", () => {
  it("asks for the editable spec by name alone", async () => {
    fakeBridge({ ok: true, name: "iron-fly-1000", automation: { symbol: "SPX" } });
    const res = await post("/api/automations/builder/draft", { name: "iron-fly-1000" });
    expect(res.statusCode).toBe(200);
    expect(seen).toEqual([{ op: "automation_draft", name: "iron-fly-1000" }]);
    expect(res.json()).toEqual({ ok: true, name: "iron-fly-1000", automation: { symbol: "SPX" } });
  });

  it("returns the module's problems and view for an edit", async () => {
    fakeBridge({ ok: true, name: "my-fly", problems: ["my-fly: missing 'legs'"], new: true });
    const res = await post("/api/automations/builder/draft", { name: "my-fly", automation: { symbol: "SPX" } });
    expect(res.json()).toEqual({ ok: true, name: "my-fly", problems: ["my-fly: missing 'legs'"], new: true });
  });

  it("refuses a body without a name or with a non-object automation", async () => {
    expect((await post("/api/automations/builder/draft", {})).statusCode).toBe(400);
    expect((await post("/api/automations/builder/draft", { name: "x", automation: [1] })).statusCode).toBe(400);
    expect(seen).toEqual([]);
  });
});

describe("the Builder's save", () => {
  it("sends only the name and the automation, and says the result is off", async () => {
    fakeBridge({ ok: true, name: "my-fly", added: true, enabled: false, backup: "b.json" });
    const res = await post("/api/automations/builder/save", { name: "my-fly", automation: { symbol: "SPX" }, enabled: true });
    expect(res.statusCode).toBe(200);
    expect(seen).toEqual([{ op: "automation_save", name: "my-fly", automation: { symbol: "SPX" } }]);
    expect(res.json()).toMatchObject({ ok: true, added: true, enabled: false });
  });

  it("passes the module's problems back as a 422", async () => {
    fakeBridge({ ok: false, code: "invalid", error: "my-fly: missing 'legs'", problems: ["my-fly: missing 'legs'"] } as unknown as BridgeResult);
    const res = await post("/api/automations/builder/save", { name: "my-fly", automation: {} });
    expect(res.statusCode).toBe(422);
    expect(res.json()).toMatchObject({ code: "invalid", problems: ["my-fly: missing 'legs'"] });
  });

  it("needs an automation, and the CSRF token", async () => {
    fakeBridge({ ok: true, name: "my-fly", added: true });
    expect((await post("/api/automations/builder/save", { name: "my-fly" })).statusCode).toBe(400);
    expect((await post("/api/automations/builder/save", { name: "my-fly", automation: {} }, { "x-csrf-token": "no" })).statusCode).toBe(403);
    expect(seen).toEqual([]);
  });
});
