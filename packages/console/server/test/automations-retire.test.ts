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
 * An automation's page retires or reinstates it: through the orchestrator's editor, carrying only the
 * automation's name and true or false, with the module's problems passed back as a list.
 */

let app: FastifyInstance;
let seen: BridgeRequest[] = [];

function fakeBridge(response: BridgeResult) {
  setBridgeCaller((req) => {
    seen.push(req);
    return response;
  });
}

const post = (payload: unknown) =>
  app.inject({
    method: "POST",
    url: "/api/automations/retire",
    payload,
    headers: { host: "127.0.0.1:5070", "x-csrf-token": CSRF_TOKEN },
  });

beforeAll(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-retire-"));
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

describe("retiring an automation", () => {
  it("sends the name and true or false, nothing else", async () => {
    fakeBridge({ ok: true, name: "iron-fly-1000", retired: "2026-11-21", notes: ["off"], backup: "b" } as unknown as BridgeResult);
    const res = await post({ name: "iron-fly-1000", retired: true, enabled: true });
    expect(res.statusCode).toBe(200);
    expect(seen).toEqual([{ op: "automation_retire", name: "iron-fly-1000", retired: true }]);
    expect(res.json()).toEqual({ ok: true, name: "iron-fly-1000", retired: "2026-11-21", notes: ["off"], backup: "b" });
  });

  it("refuses a name or a value it cannot send, before the bridge", async () => {
    fakeBridge({ ok: true } as unknown as BridgeResult);
    expect((await post({ name: "Bad Name", retired: true })).statusCode).toBe(400);
    expect((await post({ name: "iron-fly-1000", retired: "yes" })).statusCode).toBe(400);
    expect(seen).toEqual([]);
  });

  it("passes the module's problems back", async () => {
    fakeBridge({ ok: false, error: "iron-fly-1000: already retired", code: "invalid", problems: ["iron-fly-1000: already retired"] } as unknown as BridgeResult);
    const res = await post({ name: "iron-fly-1000", retired: true });
    expect(res.statusCode).toBe(422);
    expect(res.json()).toEqual({ error: "iron-fly-1000: already retired", code: "invalid", problems: ["iron-fly-1000: already retired"] });
  });
});
