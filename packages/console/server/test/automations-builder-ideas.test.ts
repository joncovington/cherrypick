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
 * The idea search routes (#1294): "Find ideas" sends a name and nothing else (the run and the split are
 * the backtest package's), and a licensed search is shown, declared from or tested only while licensed
 * results may be shown. A withheld one is never spent: the held-out test is not even asked for.
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

const ID = "ideas-mine-2611181000";
const IDEA = "vix:above:3";
const SEARCH = (licensed: boolean) => ({ ok: true, name: ID, licensed, rows: [], tried: 140 }) as BridgeResult;

const post = (url: string, payload: unknown) =>
  app.inject({ method: "POST", url, payload, headers: { host: "127.0.0.1:5070", "x-csrf-token": CSRF_TOKEN } });
const get = (url: string) => app.inject({ method: "GET", url, headers: { host: "127.0.0.1:5070" } });

beforeAll(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-builder-ideas-"));
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

describe("Find ideas", () => {
  it("sends the name alone, whatever else the page sends", async () => {
    fakeBridge({ automation_ideas: { ok: true, search: ID, task: 512, run: "builder-mine-2611161730", split: "2026-07-18" } });
    const res = await post("/api/automations/builder/ideas", { name: "mine", split: "2020-01-01", run: "x" });
    expect(res.statusCode).toBe(200);
    expect(seen).toEqual([{ op: "automation_ideas", name: "mine" }]);
    expect(res.json()).toMatchObject({ ok: true, search: ID, task: 512, split: "2026-07-18" });
  });

  it("lists a licensed search only while licensed results may be shown", async () => {
    const list = { ok: true, tasks: [], searches: [{ name: ID, licensed: true }, { name: "ideas-mine-2611171000", licensed: false }] } as BridgeResult;
    fakeBridge({ automation_idea_searches: list, features: features(false) });
    const shut = (await get("/api/automations/builder/ideas?name=mine")).json();
    expect(shut.searches.map((s: { name: string }) => s.name)).toEqual(["ideas-mine-2611171000"]);
    expect(shut.withheld).toBe(1);
    invalidateFeatures();
    fakeBridge({ automation_idea_searches: list, features: features(true) });
    expect((await get("/api/automations/builder/ideas?name=mine")).json().searches).toHaveLength(2);
  });
});

describe("Declare and the held-out test", () => {
  it("are asked for by name, search and idea once the search is readable", async () => {
    fakeBridge({
      automation_idea_search: SEARCH(false),
      automation_idea_declare: { ok: true, idea: IDEA, row: { status: "declared" } },
      features: features(false),
    });
    const res = await post("/api/automations/builder/idea-declare", { name: "mine", id: ID, idea: IDEA });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toMatchObject({ ok: true, idea: IDEA, row: { status: "declared" } });
    expect(seen.filter((r) => r.op !== "features")).toEqual([
      { op: "automation_idea_search", name: "mine", id: ID },
      { op: "automation_idea_declare", name: "mine", id: ID, idea: IDEA },
    ]);
  });

  it("never spends a withheld licensed search's held-out part", async () => {
    fakeBridge({ automation_idea_search: SEARCH(true), automation_idea_holdout: { ok: true, verdict: "passed" }, features: features(false) });
    const res = await post("/api/automations/builder/idea-holdout", { name: "mine", id: ID, idea: IDEA });
    expect(res.statusCode).toBe(403);
    expect(seen.map((r) => r.op)).not.toContain("automation_idea_holdout");
    expect((await get(`/api/automations/builder/idea-search?name=mine&id=${ID}`)).statusCode).toBe(403);
  });

  it("refuses an id or an idea shaped otherwise without asking", async () => {
    fakeBridge({});
    for (const body of [
      { name: "mine", id: "builder-mine-2611161730", idea: IDEA },
      { name: "mine", id: ID, idea: "vix; rm -rf" },
      { name: "mine", id: ID },
    ]) {
      expect((await post("/api/automations/builder/idea-holdout", body)).statusCode).toBe(400);
    }
    expect(seen).toEqual([]);
  });

  it("passes the module's refusal through", async () => {
    fakeBridge({
      automation_idea_search: SEARCH(false),
      automation_idea_holdout: { ok: false, code: "invalid", error: "this idea has had its one held-out test" },
      features: features(false),
    });
    const res = await post("/api/automations/builder/idea-holdout", { name: "mine", id: ID, idea: IDEA });
    expect(res.statusCode).toBe(422);
    expect(res.json().error).toBe("this idea has had its one held-out test");
  });
});
