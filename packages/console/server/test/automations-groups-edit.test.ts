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
 * The Automations › Groups routes: the view, the check and the save all go through the orchestrator's
 * config editor; the save sends only the groups and the moves, behind the CSRF token, and a problem the
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

const CHANGE = { groups: { ensemble: { enabled: false } }, members: { "gld-call-spread-1dte": "gold" }, switches: { "iron-fly-1000": true } };

beforeAll(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-groups-edit-"));
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

describe("the groups view", () => {
  it("reads every group and the ungrouped automations from the orchestrator", async () => {
    fakeBridge({ ok: true, groups: [{ name: "ensemble", enabled: true, members: [] }], ungrouped: [] });
    const res = await app.inject({ method: "GET", url: "/api/automations/groups/edit", headers: { host: "127.0.0.1:5070" } });
    expect(res.statusCode).toBe(200);
    expect(seen).toEqual([{ op: "automation_groups" }]);
    expect(res.json()).toEqual({ groups: [{ name: "ensemble", enabled: true, members: [] }], ungrouped: [] });
  });
});

describe("the groups check and save", () => {
  it("checks a change and writes nothing", async () => {
    fakeBridge({ ok: true, problems: [], moves: { "gld-call-spread-1dte": "gold" }, block: {} });
    const res = await post("/api/automations/groups/check", CHANGE);
    expect(seen).toEqual([{ op: "automation_groups_check", ...CHANGE }]);
    expect(res.json()).toEqual({ ok: true, problems: [], moves: { "gld-call-spread-1dte": "gold" }, switches: {}, notes: [], after: null });
  });

  it("saves only the groups, the moves and the switches, never a start from the page", async () => {
    fakeBridge({ ok: true, moved: ["gld-call-spread-1dte"], groups: ["ensemble", "gold"], backup: "b.json" });
    const res = await post("/api/automations/groups/save", { ...CHANGE, enabled: true, start: "2020-01-01", path: "/etc" });
    expect(res.statusCode).toBe(200);
    expect(seen).toEqual([{ op: "automation_groups_save", ...CHANGE }]);
    expect(res.json()).toMatchObject({ ok: true, moved: ["gld-call-spread-1dte"] });
  });

  it("passes the module's problems back as a 422", async () => {
    fakeBridge({ ok: false, code: "invalid", error: "too much", problems: ["groups.ensemble.shares: 120 percent in all, more than 100"] } as unknown as BridgeResult);
    const res = await post("/api/automations/groups/save", CHANGE);
    expect(res.statusCode).toBe(422);
    expect(res.json()).toMatchObject({ problems: ["groups.ensemble.shares: 120 percent in all, more than 100"] });
  });

  it("refuses a malformed body before asking the orchestrator", async () => {
    expect((await post("/api/automations/groups/save", { groups: [] })).statusCode).toBe(400);
    expect((await post("/api/automations/groups/save", { groups: {}, members: { x: 5 } })).statusCode).toBe(400);
    expect((await post("/api/automations/groups/save", { groups: {}, members: [] })).statusCode).toBe(400);
    expect((await post("/api/automations/groups/save", { groups: {}, switches: { x: "on" } })).statusCode).toBe(400);
    expect((await post("/api/automations/groups/save", { groups: {}, switches: [] })).statusCode).toBe(400);
    expect(seen).toEqual([]);
  });

  it("refuses a save without the CSRF token", async () => {
    fakeBridge({ ok: true });
    const res = await post("/api/automations/groups/save", CHANGE, { "x-csrf-token": "wrong" });
    expect(res.statusCode).toBe(403);
    expect(seen).toEqual([]);
  });
});
