import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Fastify, { type FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../src/config.js";
import { registerSecurity, CSRF_TOKEN } from "../src/security.js";
import { registerConfigRoutes, CHANGE_ARM_CONFIRMATION } from "../src/routes/configOps.js";
import { plainSettings } from "../src/readers/liveArm.js";
import { setBridgeCaller, type BridgeRequest, type BridgeResult } from "../src/services/configBridge.js";

/**
 * The Config page's live tab: which arm each live loop runs. The console decides nothing: the
 * orchestrator's `live_arm_options` / `set_live_arm` judge, this package composes the read, checks the
 * shapes and the literal phrase, and passes every refusal through with its meaning. What is pinned:
 * the read names each loop and says why it cannot be changed here; nothing is called without the typed
 * phrase; each refusal keeps its own status; and the console never writes the config itself.
 */

let tmp: string;
let config: ConsoleConfig;
let app: FastifyInstance;
let seen: BridgeRequest[] = [];

const MEIC = { enable_live_trading: true, live: { symbol: "SPX", arm: "pm-200" }, live_accounts: { margin: { enabled: true, symbol: "XSP", arm: "pm-xsp" } } };
const REGISTRY = {
  profiles: {
    "pm-200": { enabled: false, min_iv_rank: 0.1, per_side_stop_management: true, _note: "ignored", nested: { a: 1 } },
    "pm-1400": { enabled: false, min_iv_rank: 0.2, per_side_stop_management: true },
    "live-shadow-pm-200": { enabled: true, shadow_of: "pm-200" },
  },
};

const OPTIONS: BridgeResult = {
  ok: true,
  module: "meic",
  current: "pm-200",
  arms: [
    { arm: "pm-1400", twin: "live-shadow-pm-1400" },
    { arm: "pm-200", twin: "live-shadow-pm-200" },
  ],
  blockers: [],
  can_change: true,
};

function fakeBridge(response: BridgeResult | ((req: BridgeRequest) => BridgeResult)) {
  setBridgeCaller((req) => {
    seen.push(req);
    return typeof response === "function" ? response(req) : response;
  });
}

const get = (url: string) => app.inject({ method: "GET", url, headers: { host: "127.0.0.1:5070" } });
const post = (payload: unknown, token: string | null = CSRF_TOKEN) =>
  app.inject({
    method: "POST",
    url: "/api/config/live-arm",
    payload,
    headers: { host: "127.0.0.1:5070", ...(token === null ? {} : { "x-csrf-token": token }) },
  });

function write(rel: string, doc: unknown): void {
  fs.mkdirSync(path.dirname(path.join(tmp, rel)), { recursive: true });
  fs.writeFileSync(path.join(tmp, rel), JSON.stringify(doc));
}

beforeEach(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-live-arm-"));
  write("config.json", { modules: { meic: { enabled: true }, ladder: { enabled: true } }, broker: { logins: [{ name: "margin" }] } });
  write("config/meic.json", MEIC);
  write("config/ladder.json", { live: { arm: "session", symbols: ["TQQQ"] } });
  write("config.risk.json", REGISTRY);
  fs.mkdirSync(path.join(tmp, "meic"), { recursive: true });
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
      manageDir: path.join(tmp, "manage"),
      overviewDir: path.join(tmp, "overview"),
      technicalsDir: path.join(tmp, "technicals"),
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

afterEach(async () => {
  setBridgeCaller();
  seen = [];
  await app.close();
});

describe("GET /api/config/live-arm", () => {
  it("names each live loop, offers MEIC's main arms with their twins and settings, and marks the rest by hand", async () => {
    fakeBridge(OPTIONS);
    const body = (await get("/api/config/live-arm")).json();
    expect(body.bridge).toBe("ok");
    expect(body.loops.map((l: { loop: string }) => l.loop)).toEqual(["meic", "meic.margin", "ladder"]);
    const [meic, margin, ladder] = body.loops;
    expect(meic).toMatchObject({ label: "meic · SPX", current: "pm-200", editable: true, canChange: true, blockers: [] });
    expect(meic.arms.map((a: { arm: string }) => a.arm)).toEqual(["pm-1400", "pm-200"]);
    expect(meic.arms[1]).toMatchObject({ twin: "live-shadow-pm-200", settings: { min_iv_rank: 0.1, per_side_stop_management: true } });
    // Notes, markers and nested values are not settings.
    expect(Object.keys(meic.arms[1].settings)).toEqual(["min_iv_rank", "per_side_stop_management"]);
    expect(margin).toMatchObject({ editable: false, current: "pm-xsp", canChange: false, arms: [] });
    expect(margin.note).toContain("edited by hand");
    expect(ladder).toMatchObject({ label: "ladder", current: "session", editable: false });
    expect(ladder.note).toContain("edited by hand");
    expect(seen).toEqual([{ op: "live_arm_options" }]);
  });

  it("says why a change is refused right now, and cannot be changed while it is", async () => {
    fakeBridge({ ...OPTIONS, ok: true, blockers: ["1 position(s) open"], can_change: false } as BridgeResult);
    const [meic] = (await get("/api/config/live-arm")).json().loops;
    expect(meic).toMatchObject({ canChange: false, blockers: ["1 position(s) open"], editable: true });
  });

  it("offers nothing to choose when no arm has a paper twin, and says so", async () => {
    fakeBridge({ ...OPTIONS, arms: [] } as BridgeResult);
    const [meic] = (await get("/api/config/live-arm")).json().loops;
    expect(meic).toMatchObject({ canChange: false, arms: [] });
    expect(meic.note).toContain("paper twin");
  });

  it("is not editable, and says so, when the orchestrator cannot be reached", async () => {
    fakeBridge({ ok: false, code: "unavailable", error: "config bridge unavailable" });
    const body = (await get("/api/config/live-arm")).json();
    expect(body.bridge).toBe("unavailable");
    expect(body.error).toContain("unavailable");
    expect(body.loops[0]).toMatchObject({ loop: "meic", editable: false, canChange: false, current: "pm-200" });
  });

  it("hides the ladder while its module is off, and everything when MEIC has no live block", async () => {
    write("config.json", { modules: { meic: { enabled: true }, ladder: { enabled: false } }, broker: { logins: [{ name: "margin" }] } });
    fakeBridge(OPTIONS);
    expect((await get("/api/config/live-arm")).json().loops.map((l: { loop: string }) => l.loop)).toEqual(["meic", "meic.margin"]);
    write("config/meic.json", { enable_live_trading: false });
    expect((await get("/api/config/live-arm")).json().loops).toEqual([]);
  });
});

describe("POST /api/config/live-arm", () => {
  it("asks the orchestrator for exactly one change, with the typed phrase, and answers with the page's own read", async () => {
    fakeBridge((req) => (req.op === "set_live_arm" ? { ok: true, changed: true, from: "pm-200", arm: "pm-1400", note: "takes effect at the next arming" } : OPTIONS));
    const res = await post({ arm: "pm-1400", confirm: CHANGE_ARM_CONFIRMATION });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toMatchObject({ ok: true, changed: true, from: "pm-200", arm: "pm-1400", note: "takes effect at the next arming" });
    expect(res.json().payload.loops[0].loop).toBe("meic");
    expect(seen[0]).toEqual({ op: "set_live_arm", module: "meic", arm: "pm-1400", confirm: "CHANGE ARM" });
  });

  it("never calls the orchestrator without the literal phrase, or with a malformed arm", async () => {
    fakeBridge(OPTIONS);
    for (const body of [
      { arm: "pm-1400" },
      { arm: "pm-1400", confirm: "change arm" },
      { arm: "pm-1400", confirm: "CHANGE ARM " },
      { arm: "pm-1400", confirm: true },
    ]) {
      const res = await post(body);
      expect(res.statusCode).toBe(400);
      expect(res.json().code).toBe("confirm_required");
    }
    for (const arm of ["", "../meic", "a b", "x".repeat(65), 7, null]) {
      const res = await post({ arm, confirm: CHANGE_ARM_CONFIRMATION });
      expect(res.statusCode).toBe(400);
      expect(res.json().code).toBe("bad_request");
    }
    expect(seen).toEqual([]);
  });

  it("refuses without the CSRF token, and calls nothing", async () => {
    fakeBridge(OPTIONS);
    expect((await post({ arm: "pm-1400", confirm: CHANGE_ARM_CONFIRMATION }, null)).statusCode).toBe(403);
    expect(seen).toEqual([]);
  });

  const refusals: Array<[string, number]> = [
    ["confirm_required", 400],
    ["unsupported", 400],
    ["unknown_arm", 422],
    ["no_twin", 422],
    ["not_flat", 409],
    ["status_unreadable", 503],
    ["journal_unwritable", 500],
    ["unavailable", 502],
  ];
  for (const [code, status] of refusals) {
    it(`a ${code} refusal reaches the page as ${String(status)}, with its words`, async () => {
      fakeBridge({ ok: false, code: code as never, error: `the orchestrator said ${code}`, ...(code === "not_flat" ? { blockers: ["1 position(s) open"] } : {}) });
      const res = await post({ arm: "pm-1400", confirm: CHANGE_ARM_CONFIRMATION });
      expect(res.statusCode).toBe(status);
      expect(res.json()).toMatchObject({ code, error: `the orchestrator said ${code}` });
      if (code === "not_flat") expect(res.json().blockers).toEqual(["1 position(s) open"]);
    });
  }

  it("never writes the config itself: the file is the same after a change the orchestrator reports", async () => {
    const before = fs.readFileSync(path.join(tmp, "config", "meic.json"), "utf-8");
    fakeBridge((req) => (req.op === "set_live_arm" ? { ok: true, changed: true, from: "pm-200", arm: "pm-1400", note: null } : OPTIONS));
    await post({ arm: "pm-1400", confirm: CHANGE_ARM_CONFIRMATION });
    expect(fs.readFileSync(path.join(tmp, "config", "meic.json"), "utf-8")).toBe(before);
  });
});

describe("plainSettings", () => {
  it("keeps strings, numbers, booleans and null; drops notes, markers and nested values", () => {
    expect(plainSettings({ enabled: true, shadow_of: "x", follows_live: true, _note: "n", a: 1, b: "s", c: false, d: null, e: { x: 1 }, f: [1] })).toEqual({ a: 1, b: "s", c: false, d: null });
    expect(plainSettings(null)).toEqual({});
  });
});
