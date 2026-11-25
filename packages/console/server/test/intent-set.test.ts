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
 * A trade's intent, answered on the position: one narrow write through the orchestrator's editor
 * (configcli `intent_set`, `orchestrator.intentedit`). The route carries a trade key and an intent and
 * nothing else, checks their shape before the bridge, passes the editor's refusals back with their
 * meaning, and answers only a POST that carries the session's CSRF token on a loopback Host.
 */

const ID = "XYZ-261001-1";
const LONG = "XYZ 280121C00040000";

let app: FastifyInstance;
let seen: BridgeRequest[] = [];

function fakeBridge(response: BridgeResult) {
  setBridgeCaller((req) => {
    seen.push(req);
    return response;
  });
}

const post = (payload: unknown, headers: Record<string, string> = { "x-csrf-token": CSRF_TOKEN }) =>
  app.inject({ method: "POST", url: "/api/config/intent", payload, headers: { host: "127.0.0.1:5070", ...headers } });

beforeAll(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-intent-"));
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

describe("answering a trade's intent", () => {
  it("sends the trade and the intent, nothing else, and returns the value it replaced", async () => {
    fakeBridge({ ok: true, changed: true, trade: ID, intent: "wheel", previous: null, backup: "b", mtime: 5 } as unknown as BridgeResult);
    const res = await post({ trade: ID, intent: "wheel", positions: { XYZ: { intent: "wheel" } }, key: "live.arm", extra: 1 });
    expect(res.statusCode).toBe(200);
    expect(seen).toEqual([{ op: "intent_set", trade: ID, intent: "wheel" }]);
    expect(res.json()).toEqual({ ok: true, changed: true, trade: ID, intent: "wheel", previous: null, backup: "b", mtime: 5 });
  });

  it("carries the page's version of the file when it has one", async () => {
    fakeBridge({ ok: true, changed: true, trade: LONG, intent: "pmcc", previous: "diagonal" } as unknown as BridgeResult);
    const res = await post({ trade: LONG, intent: "pmcc", expectedMtime: 1_800_000_000_000_000_000 });
    expect(seen).toEqual([{ op: "intent_set", trade: LONG, intent: "pmcc", expected_mtime: 1_800_000_000_000_000_000 }]);
    // The value it replaced comes back, which is what Undo writes.
    expect(res.json()).toMatchObject({ intent: "pmcc", previous: "diagonal" });
  });

  it("treats a null version as none", async () => {
    fakeBridge({ ok: true, changed: true, trade: ID, intent: "wheel", previous: null } as unknown as BridgeResult);
    const res = await post({ trade: ID, intent: "wheel", expectedMtime: null });
    expect(res.statusCode).toBe(200);
    expect(seen).toEqual([{ op: "intent_set", trade: ID, intent: "wheel" }]);
  });

  it("clears with null, and returns the intent it cleared", async () => {
    fakeBridge({ ok: true, changed: true, trade: ID, intent: null, previous: "wheel" } as unknown as BridgeResult);
    const res = await post({ trade: ID, intent: null });
    expect(res.statusCode).toBe(200);
    expect(seen).toEqual([{ op: "intent_set", trade: ID, intent: null }]);
    expect(res.json()).toMatchObject({ intent: null, previous: "wheel" });
  });

  it.each([
    ["a provisional ID", { trade: "XYZ-open-ceda13", intent: "wheel" }],
    ["a path", { trade: "../XYZ-261001-1", intent: "wheel" }],
    ["a pointer", { trade: "/live/arm", intent: "wheel" }],
    ["a key into an entry", { trade: `${ID}/intent`, intent: "wheel" }],
    ["a put contract", { trade: "XYZ 270319P00013000", intent: "wheel" }],
    ["a name, not a trade", { trade: "XYZ", intent: "wheel" }],
    ["no trade", { intent: "wheel" }],
    ["a trade that is not text", { trade: 7, intent: "wheel" }],
    ["a label", { trade: ID, intent: "income" }],
    ["no intent", { trade: ID }],
    ["an empty intent", { trade: ID, intent: "" }],
    ["an intent that is not text", { trade: ID, intent: true }],
    ["a version that is not a number", { trade: ID, intent: "wheel", expectedMtime: "later" }],
  ])("refuses %s before the bridge", async (_why, body) => {
    fakeBridge({ ok: true } as unknown as BridgeResult);
    expect((await post(body)).statusCode).toBe(400);
    expect(seen).toEqual([]);
  });

  it("passes the editor's refusals back with their meaning", async () => {
    const cases: Array<[BridgeResult, number]> = [
      [{ ok: false, code: "conflict", error: "changed on disk" } as BridgeResult, 409],
      [{ ok: false, code: "invalid", error: "intent: wheel or no_wheel" } as BridgeResult, 422],
      [{ ok: false, code: "not_found", error: "manage has no readable config" } as BridgeResult, 404],
      [{ ok: false, code: "unavailable", error: "config bridge unavailable" } as BridgeResult, 502],
    ];
    for (const [response, status] of cases) {
      fakeBridge(response);
      const res = await post({ trade: ID, intent: "pmcc" });
      expect(res.statusCode).toBe(status);
      expect(res.json()).toMatchObject({ code: (response as { code: string }).code, error: (response as { error: string }).error });
    }
  });
});

describe("who may answer", () => {
  it("refuses a POST without the session's CSRF token, before the bridge", async () => {
    fakeBridge({ ok: true } as unknown as BridgeResult);
    expect((await post({ trade: ID, intent: "wheel" }, {})).statusCode).toBe(403);
    expect((await post({ trade: ID, intent: "wheel" }, { "x-csrf-token": "wrong" })).statusCode).toBe(403);
    expect(seen).toEqual([]);
  });

  it("refuses a Host that is not loopback", async () => {
    fakeBridge({ ok: true } as unknown as BridgeResult);
    const res = await app.inject({
      method: "POST",
      url: "/api/config/intent",
      payload: { trade: ID, intent: "wheel" },
      headers: { host: "evil.example:5070", "x-csrf-token": CSRF_TOKEN },
    });
    expect(res.statusCode).toBe(403);
    expect(seen).toEqual([]);
  });

  it("is not reachable by a GET", async () => {
    fakeBridge({ ok: true } as unknown as BridgeResult);
    const res = await app.inject({ method: "GET", url: "/api/config/intent", headers: { host: "127.0.0.1:5070" } });
    expect(res.statusCode).toBe(404);
    expect(seen).toEqual([]);
  });
});
