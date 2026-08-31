import { describe, it, expect, beforeAll, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Fastify, { type FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../src/config.js";
import { registerSecurity, CSRF_TOKEN } from "../src/security.js";
import { registerConfigRoutes, RESUME_CONFIRMATION } from "../src/routes/configOps.js";
import { setBridgeCaller, type BridgeRequest, type BridgeResult } from "../src/services/configBridge.js";

/**
 * The Config page's write surface. Two things it must get right: a refusal from the orchestrator's
 * config editor reaches the browser as the RIGHT kind of failure (a guarded pointer is not a
 * validation error is not a stale-file conflict), and the halt flag's friction is asymmetric —
 * one click to stop, a typed confirmation to resume.
 */

let config: ConsoleConfig;
let app: FastifyInstance;
let seen: BridgeRequest[] = [];

function fakeBridge(response: BridgeResult | ((req: BridgeRequest) => BridgeResult)) {
  setBridgeCaller((req) => {
    seen.push(req);
    return typeof response === "function" ? response(req) : response;
  });
}

const post = (url: string, payload: unknown) =>
  app.inject({ method: "POST", url, payload, headers: { host: "127.0.0.1:5070", "x-csrf-token": CSRF_TOKEN } });

beforeAll(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-config-test-"));
  fs.mkdirSync(path.join(tmp, "state"), { recursive: true });
  fs.writeFileSync(path.join(tmp, "config.json"), JSON.stringify({ modules: { flies: { enabled: true } } }));
  config = {
    port: 0,
    paths: {
      cherrypick: tmp,
      streamCacheDb: path.join(tmp, "stream_cache.db"),
      watchdogLast: path.join(tmp, "watchdog.last.json"),
      orchestratorConfig: path.join(tmp, "config.json"),
      consoleData: path.join(tmp, "console"),
      meicDir: path.join(tmp, "meic"),
      fliesDir: path.join(tmp, "flies"),
      earningsDir: path.join(tmp, "earnings"),
      gexDir: path.join(tmp, "gex"),
      reviewDir: path.join(tmp, "review"),
      overviewDir: path.join(tmp, "overview"),
      advisorDir: path.join(tmp, "advisor"),
      adviceDir: path.join(tmp, "state", "advice"),
      meicRiskConfig: path.join(tmp, "config.risk.json"),
      fliesConfig: path.join(tmp, "config", "flies.json"),
    },
  };
  app = Fastify();
  registerSecurity(app);
  registerConfigRoutes(app, config);
  await app.ready();
});

afterEach(() => {
  setBridgeCaller();
  seen = [];
});

describe("refusals keep their meaning across the bridge", () => {
  const cases: Array<[BridgeResult["ok"] extends never ? never : string, string, number]> = [
    ["guarded", "/live/enabled is guarded", 403],
    ["conflict", "changed on disk — reload before saving", 409],
    ["invalid", "config root must be a JSON object", 422],
    ["not_found", "pointer not found: /nope", 404],
    ["unavailable", "config bridge unavailable", 502],
  ];

  for (const [code, error, status] of cases) {
    it(`${code} → ${String(status)}`, async () => {
      fakeBridge({ ok: false, code: code as never, error });
      const res = await post("/api/config/save", {
        target: "flies",
        edits: [{ pointer: "/arms/gex/enabled", value: true }],
      });
      expect(res.statusCode).toBe(status);
      expect(res.json()).toMatchObject({ code, error });
    });
  }

  it("a successful save returns the new mtime and the backup it took", async () => {
    fakeBridge({ ok: true, mtime: 42, backup: "~/.cherrypick/state/config-backups/flies.x.json", issues: [] });
    const res = await post("/api/config/save", {
      target: "flies",
      expectedMtime: 41,
      edits: [{ pointer: "/arms/gex/enabled", value: false }],
    });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toMatchObject({ ok: true, mtime: 42 });
    expect(seen[0]).toMatchObject({ op: "save", target: "flies", expected_mtime: 41 });
  });
});

describe("save requests are checked before they reach the config editor", () => {
  it("an unknown target never becomes a subprocess", async () => {
    fakeBridge({ ok: true });
    const res = await post("/api/config/save", { target: "desk", edits: [{ pointer: "/enabled", value: true }] });
    expect(res.statusCode).toBe(400);
    expect(seen).toHaveLength(0);
  });

  it("an edit without a JSON pointer is rejected", async () => {
    fakeBridge({ ok: true });
    const res = await post("/api/config/save", { target: "meic", edits: [{ pointer: "symbols", value: [] }] });
    expect(res.statusCode).toBe(400);
    expect(seen).toHaveLength(0);
  });

  it("an empty edit list is rejected", async () => {
    fakeBridge({ ok: true });
    expect((await post("/api/config/save", { target: "meic", edits: [] })).statusCode).toBe(400);
    expect(seen).toHaveLength(0);
  });
});

describe("the halt flag's friction is asymmetric", () => {
  it("halting takes one click and no confirmation", async () => {
    fakeBridge({ ok: true, present: true });
    const res = await post("/api/config/lock", { present: true });
    expect(res.statusCode).toBe(200);
    expect(seen[0]).toEqual({ op: "set_halt", present: true });
  });

  it("resuming without the typed confirmation is refused, and never reaches the flag", async () => {
    fakeBridge({ ok: true, present: false });
    for (const body of [{ present: false }, { present: false, confirm: "" }, { present: false, confirm: "resume live" }]) {
      const res = await post("/api/config/lock", body);
      expect(res.statusCode).toBe(400);
      expect(res.json()).toMatchObject({ code: "confirm_required" });
    }
    expect(seen, "the halt flag must not be touched by a rejected resume").toHaveLength(0);
  });

  it("resuming with the exact confirmation goes through", async () => {
    fakeBridge({ ok: true, present: false });
    const res = await post("/api/config/lock", { present: false, confirm: RESUME_CONFIRMATION });
    expect(res.statusCode).toBe(200);
    expect(seen[0]).toEqual({ op: "set_halt", present: false });
    // The response is the freshly-read lock status, so the hero can't show a stale state.
    expect(res.json()).toHaveProperty("modules");
  });

  it("a missing 'present' is a bad request, not a guess", async () => {
    fakeBridge({ ok: true });
    expect((await post("/api/config/lock", {})).statusCode).toBe(400);
    expect(seen).toHaveLength(0);
  });
});

describe("the config routes sit behind the mutating-surface gate", () => {
  it("a POST without the CSRF token is refused", async () => {
    fakeBridge({ ok: true });
    const res = await app.inject({
      method: "POST",
      url: "/api/config/lock",
      payload: { present: true },
      headers: { host: "127.0.0.1:5070" },
    });
    expect(res.statusCode).toBe(403);
    expect(seen).toHaveLength(0);
  });
});

describe("the model read", () => {
  it("reports a target the editor could not load without failing the whole page", async () => {
    fakeBridge((req) =>
      req.op === "load" && req.target === "meic-risk"
        ? { ok: false, code: "not_found", error: "meic is not configured" }
        : { ok: true, exists: true, doc: { symbols: ["SPX"] }, mtime: 7, guarded: [], issues: [] },
    );
    const res = await app.inject({ method: "GET", url: "/api/config/model", headers: { host: "127.0.0.1:5070" } });
    expect(res.statusCode).toBe(200);
    const body = res.json() as { targets: Record<string, { exists: boolean; error?: string; mtime: number | null }> };
    expect(body.targets["meic"]).toMatchObject({ exists: true, mtime: 7 });
    expect(body.targets["meic-risk"]).toMatchObject({ exists: false, error: "meic is not configured" });
  });
});

describe("console prefs", () => {
  it("round-trip through the console's own store", async () => {
    const res = await post("/api/config/prefs", { key: "denseTables", value: true });
    expect(res.statusCode).toBe(200);
    expect((res.json() as { prefs: Record<string, unknown> }).prefs["denseTables"]).toBe(true);

    const read = await app.inject({ method: "GET", url: "/api/config/prefs", headers: { host: "127.0.0.1:5070" } });
    expect((read.json() as { prefs: Record<string, unknown> }).prefs["denseTables"]).toBe(true);
  });

  it("a keyless write is refused", async () => {
    expect((await post("/api/config/prefs", { value: 1 })).statusCode).toBe(400);
  });
});
