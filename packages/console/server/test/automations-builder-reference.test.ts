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
 * The Builder's pin of a backtest reference: through the orchestrator's editor, carrying only the
 * automation's name and one of its own Builder runs (or null to unpin), with the module's problems
 * passed back as a list.
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
    url: "/api/automations/builder/reference",
    payload,
    headers: { host: "127.0.0.1:5070", "x-csrf-token": CSRF_TOKEN },
  });

beforeAll(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-builder-reference-"));
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

describe("the Builder's reference pin", () => {
  it("sends the name and the run, nothing else", async () => {
    fakeBridge({ ok: true, name: "mine", run: "builder-mine-2611201000" } as unknown as BridgeResult);
    const res = await post({ name: "mine", run: "builder-mine-2611201000", enabled: true });
    expect(res.statusCode).toBe(200);
    expect(seen).toEqual([{ op: "automation_reference", name: "mine", run: "builder-mine-2611201000" }]);
    expect(res.json()).toEqual({ ok: true, name: "mine", run: "builder-mine-2611201000" });
  });

  it("unpins with null", async () => {
    fakeBridge({ ok: true, name: "mine", run: null } as unknown as BridgeResult);
    expect((await post({ name: "mine", run: null })).statusCode).toBe(200);
    expect(seen).toEqual([{ op: "automation_reference", name: "mine", run: null }]);
  });

  it("refuses another automation's run, a hand-named file and a bad name before the bridge", async () => {
    for (const body of [
      { name: "mine", run: "builder-other-2611201000" },
      { name: "mine", run: "my-hand-run" },
      { name: "mine", run: "builder-mine-2611201000/../x" },
      { name: "../x", run: null },
      { run: null },
    ]) {
      expect((await post(body)).statusCode).toBe(400);
    }
    expect(seen).toEqual([]);
  });

  it("passes the module's problems back", async () => {
    fakeBridge({ ok: false, code: "not_found", error: "no Builder run builder-mine-2611201000 for mine" } as unknown as BridgeResult);
    const res = await post({ name: "mine", run: "builder-mine-2611201000" });
    expect(res.statusCode).toBe(404);
    expect(res.json()).toMatchObject({ code: "not_found" });
  });
});
