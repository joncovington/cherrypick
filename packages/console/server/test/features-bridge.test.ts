import { describe, it, expect, beforeAll, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Fastify, { type FastifyInstance } from "fastify";
import type { SuiteFeatures, SuiteFeaturesOk } from "@console/shared";
import type { ConsoleConfig } from "../src/config.js";
import { registerSecurity, CSRF_TOKEN } from "../src/security.js";
import { registerConfigRoutes, RESUME_CONFIRMATION } from "../src/routes/configOps.js";
import { setBridgeCaller, type BridgeRequest, type BridgeResult } from "../src/services/configBridge.js";
import { getFeatures, invalidateFeatures, moduleOn, parseFeatures, TTL_MS } from "../src/services/featuresBridge.js";
import { readDesk, readDeskLive } from "../src/readers/desk.js";
import { readLogTail } from "../src/readers/logs.js";

/**
 * "Is this module off?" is the orchestrator's answer (`configcli` op `features`), asked through the
 * config bridge and memoised. Three things to get right here: the reply parses without inventing an
 * "off", the memo is dropped the moment the Config page writes, and the Overview's rows for an off
 * module never leave the server — while an unknown answer keeps every row.
 */

const REPLY = {
  ok: true as const,
  capabilities: { claude: true, dolt: false },
  modules: {
    meic: { configured: true, enabled: true, missing: [] },
    flies: { configured: true, enabled: true, missing: [] },
    calendars: { configured: true, enabled: true, missing: [] },
    pmcc: { configured: false, enabled: false, missing: [] },
    curve: { configured: true, enabled: true, missing: [] },
    bwb: { configured: true, enabled: true, missing: [] },
    earnings: { configured: true, enabled: false, missing: ["dolt"] },
  },
  services: { "gex-recorder": false, "meic-sidecar": false },
  features: { advisor: true, review_narrative: true, morning_narrative: false, technicals: true },
};

let calls: BridgeRequest[] = [];
function fakeBridge(fn: (req: BridgeRequest) => BridgeResult) {
  setBridgeCaller((req) => {
    calls.push(req);
    return fn(req);
  });
}

afterEach(() => {
  setBridgeCaller();
  invalidateFeatures();
  calls = [];
});

describe("parsing the orchestrator's reply", () => {
  it("carries every map through", () => {
    const f = parseFeatures(REPLY) as SuiteFeaturesOk;
    expect(f.ok).toBe(true);
    expect(f.modules["earnings"]).toEqual({ configured: true, enabled: false, missing: ["dolt"] });
    expect(f.services["gex-recorder"]).toBe(false);
    expect(f.features["morning_narrative"]).toBe(false);
    expect(f.capabilities["dolt"]).toBe(false);
  });

  it("a bridge failure is a value, never a throw", () => {
    expect(parseFeatures({ ok: false, code: "unavailable", error: "no python" })).toEqual({ ok: false, error: "no python" });
  });

  it("a reply with no modules map is a failure, not 'everything off'", () => {
    expect(parseFeatures({ ok: true, features: {} }).ok).toBe(false);
  });

  it("a module row without a boolean enabled is left out, so it reads as visible", () => {
    const f = parseFeatures({ ok: true, modules: { meic: { configured: true, enabled: "no" } } }) as SuiteFeaturesOk;
    expect(f.modules["meic"]).toBeUndefined();
    expect(moduleOn(f, "meic")).toBe(true);
  });
});

describe("moduleOn", () => {
  const f = parseFeatures(REPLY);
  it("follows enabled for a trading module and the recorder for gex", () => {
    expect(moduleOn(f, "meic")).toBe(true);
    expect(moduleOn(f, "earnings")).toBe(false);
    expect(moduleOn(f, "pmcc")).toBe(false);
    expect(moduleOn(f, "gex")).toBe(false);
  });
  it("is true for anything unknown", () => {
    expect(moduleOn(undefined, "earnings")).toBe(true);
    expect(moduleOn({ ok: false, error: "x" }, "earnings")).toBe(true);
    expect(moduleOn(f, "watchdog")).toBe(true);
  });
});

describe("the memo", () => {
  it("asks once per window, and again after it", () => {
    fakeBridge(() => REPLY);
    const t0 = 1_000_000;
    getFeatures(t0);
    getFeatures(t0 + TTL_MS - 1);
    expect(calls).toHaveLength(1);
    expect(calls[0]).toEqual({ op: "features" });
    getFeatures(t0 + TTL_MS);
    expect(calls).toHaveLength(2);
  });

  it("is dropped by invalidateFeatures", () => {
    fakeBridge(() => REPLY);
    getFeatures(5);
    invalidateFeatures();
    getFeatures(6);
    expect(calls).toHaveLength(2);
  });
});

describe("the routes", () => {
  let app: FastifyInstance;
  beforeAll(async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-features-"));
    fs.mkdirSync(path.join(tmp, "state"), { recursive: true });
    app = Fastify();
    registerSecurity(app);
    registerConfigRoutes(app, configFor(tmp));
    await app.ready();
  });
  const post = (url: string, payload: unknown) =>
    app.inject({ method: "POST", url, payload, headers: { host: "127.0.0.1:5070", "x-csrf-token": CSRF_TOKEN } });
  const featureCalls = () => calls.filter((c) => c.op === "features").length;

  it("GET /api/features is 200 with ok:false when the bridge fails, so the browser can fail open", async () => {
    fakeBridge(() => ({ ok: false, code: "unavailable", error: "config bridge unavailable" }));
    const res = await app.inject({ method: "GET", url: "/api/features", headers: { host: "127.0.0.1:5070" } });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toEqual({ ok: false, error: "config bridge unavailable" });
  });

  it("a config save drops the memo, so the next read asks again", async () => {
    fakeBridge((req) => (req.op === "save" ? { ok: true, mtime: 2 } : REPLY));
    const get = () => app.inject({ method: "GET", url: "/api/features", headers: { host: "127.0.0.1:5070" } });
    await get();
    await get();
    expect(featureCalls()).toBe(1);
    const saved = await post("/api/config/save", { target: "orchestrator", edits: [{ pointer: "/modules/pmcc/enabled", value: false }] });
    expect(saved.statusCode).toBe(200);
    await get();
    expect(featureCalls()).toBe(2);
  });

  it("a refused save does not (nothing was written)", async () => {
    fakeBridge((req) => (req.op === "save" ? { ok: false, code: "conflict", error: "changed on disk" } : REPLY));
    const get = () => app.inject({ method: "GET", url: "/api/features", headers: { host: "127.0.0.1:5070" } });
    await get();
    await post("/api/config/save", { target: "orchestrator", edits: [{ pointer: "/modules/pmcc/enabled", value: false }] });
    await get();
    expect(featureCalls()).toBe(1);
  });

  it("a halt toggle drops the memo", async () => {
    fakeBridge(() => REPLY);
    const get = () => app.inject({ method: "GET", url: "/api/features", headers: { host: "127.0.0.1:5070" } });
    await get();
    await post("/api/config/lock", { present: false, confirm: RESUME_CONFIRMATION });
    await get();
    expect(featureCalls()).toBe(2);
  });
});

function configFor(tmp: string): ConsoleConfig {
  return {
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
      technicalsDir: path.join(tmp, "technicals"),
      advisorDir: path.join(tmp, "advisor"),
      adviceDir: path.join(tmp, "state", "advice"),
      meicRiskConfig: path.join(tmp, "config.risk.json"),
      fliesConfig: path.join(tmp, "config", "flies.json"),
      calendarsDir: path.join(tmp, "calendars"),
      pmccDir: path.join(tmp, "pmcc"),
      curveDir: path.join(tmp, "curve"),
      bwbDir: path.join(tmp, "bwb"),
      pmccConfigCandidates: [],
      calendarsConfigCandidates: [],
      curveConfigCandidates: [],
    },
  } as ConsoleConfig;
}

describe("the Overview's desk leaves off modules out on the server", () => {
  const features: SuiteFeatures = parseFeatures(REPLY);

  function writeFactSet(dir: string, session: string): void {
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(
      path.join(dir, `eod-${session}.json`),
      JSON.stringify({
        modules: {
          meic: { ok: true, results: { net: 10, closed: 1, wins: 1, losses: 0 } },
          earnings: { ok: true, results: { net: 5, closed: 1, wins: 1, losses: 0 } },
        },
      }),
    );
  }

  it("paper: liveness, exposure, entries, evidence and eod drop earnings, pmcc and gex; the streamer stays", () => {
    const config = configFor(fs.mkdtempSync(path.join(os.tmpdir(), "console-features-desk-")));
    writeFactSet(config.paths.reviewDir, "2026-09-30");
    const desk = readDesk(config, features);
    const liveness = desk.liveness.map((r) => r.id);
    expect(liveness).toContain("streamer");
    expect(liveness).toContain("meic");
    for (const off of ["earnings", "pmcc", "gex"]) expect(liveness).not.toContain(off);
    expect(desk.exposure.map((r) => r.module)).toEqual(["meic", "flies", "calendars", "curve", "bwb"]);
    expect(desk.entries.map((r) => r.module)).toEqual(["meic", "flies", "calendars", "curve", "bwb"]);
    expect(desk.evidence.map((r) => r.module)).toEqual(["meic"]);
    expect(desk.eod.rows.map((r) => r.module)).toEqual(["meic"]);
  });

  it("live: exposure and entries drop the same modules", () => {
    const config = configFor(fs.mkdtempSync(path.join(os.tmpdir(), "console-features-live-")));
    const live = readDeskLive(config, features);
    expect(live.exposure.map((r) => r.module)).toEqual(["meic", "flies", "calendars", "curve", "bwb"]);
    expect(live.entries.map((r) => r.module)).toEqual(["meic", "flies", "calendars", "curve", "bwb"]);
  });

  it("an unknown answer keeps every row (fail open)", () => {
    const config = configFor(fs.mkdtempSync(path.join(os.tmpdir(), "console-features-open-")));
    for (const f of [undefined, { ok: false as const, error: "bridge down" }]) {
      const desk = readDesk(config, f);
      expect(desk.exposure).toHaveLength(7);
      expect(desk.liveness.map((r) => r.id)).toContain("gex");
      expect(readDeskLive(config, f).exposure).toHaveLength(7);
    }
  });
});

describe("the merged log tail", () => {
  it("leaves an off module out of the default sources, and still reads it when asked by name", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-features-logs-"));
    const config = configFor(tmp);
    const logs = path.join(tmp, "logs");
    fs.mkdirSync(logs, { recursive: true });
    fs.writeFileSync(path.join(logs, "earnings_paper.log"), "2026-09-30 10:00:00 INFO earnings tick\n");
    fs.mkdirSync(path.join(logs, "meic"), { recursive: true });
    fs.writeFileSync(path.join(logs, "meic", "paper_loop.log"), "2026-09-30 10:00:01 INFO meic tick\n");
    const features = parseFeatures(REPLY);
    expect(readLogTail(config, 50, undefined, features).map((l) => l.source)).toEqual(["meic"]);
    expect(readLogTail(config, 50, undefined, undefined).map((l) => l.source).sort()).toEqual(["earnings", "meic"]);
    expect(readLogTail(config, 50, "earnings", features).map((l) => l.source)).toEqual(["earnings"]);
  });
});
