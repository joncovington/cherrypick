import { describe, it, expect, beforeAll, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Fastify, { type FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../src/config.js";
import { registerSecurity } from "../src/security.js";
import { registerPerformanceRoutes } from "../src/routes/performance.js";
import { setMetricsCaller, resetMetricsCache } from "../src/services/metricsBridge.js";

/**
 * `GET /api/performance/:module` is the one HTTP door onto every module's calibration reading.
 * What matters here: an unknown module 404s by name rather than 500ing or silently reading some
 * other module's ledger, `?era=ALL` actually reaches the reader, and a working module reads
 * through cleanly. The reader itself (module->schema->dbPath, era bounding) is unit-tested in
 * performance-reader.test.ts; this only pins the route's own plumbing.
 */

let app: FastifyInstance;
let tmp: string;

beforeAll(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-perfroute-"));
  fs.writeFileSync(path.join(tmp, "config.json"), JSON.stringify({ data_epoch: { date: "2026-08-21" } }));
  const config = {
    port: 0,
    paths: {
      cherrypick: tmp,
      streamCacheDb: "",
      watchdogLast: "",
      orchestratorConfig: path.join(tmp, "config.json"),
      consoleData: "",
      meicDir: path.join(tmp, "meic"),
      fliesDir: path.join(tmp, "flies"),
      earningsDir: path.join(tmp, "earnings"),
      calendarsDir: path.join(tmp, "calendars"),
      pmccDir: path.join(tmp, "pmcc"),
      curveDir: path.join(tmp, "curve"),
      bwbDir: path.join(tmp, "bwb"),
      gexDir: "",
      reviewDir: "",
      overviewDir: "",
      advisorDir: "",
      adviceDir: "",
      meicRiskConfig: "",
      fliesConfig: "",
      pmccConfigCandidates: [],
      calendarsConfigCandidates: [],
      curveConfigCandidates: [],
    },
  } as unknown as ConsoleConfig;

  app = Fastify();
  registerSecurity(app);
  registerPerformanceRoutes(app, config);
  await app.ready();
});

afterEach(() => {
  setMetricsCaller();
  resetMetricsCache();
});

const get = (url: string) => app.inject({ method: "GET", url, headers: { host: "127.0.0.1:5070" } });

describe("the performance route", () => {
  it("404s an unknown module by name, rather than reading some other ledger", async () => {
    const res = await get("/api/performance/not_a_real_module");
    expect(res.statusCode).toBe(404);
    expect(res.json().error).toContain("not_a_real_module");
  });

  it("serves a known module's reading", async () => {
    setMetricsCaller(() => ({
      ok: true,
      error: null,
      metrics: { schema: "curve_vx", n_records: 1, groups: { control: { reading: { sample: 1 }, session_nets: [], trade_nets: [5.0] } } },
    }));
    const res = await get("/api/performance/curve");
    expect(res.statusCode).toBe(200);
    const body = res.json();
    expect(body.ok).toBe(true);
    expect(body.module).toBe("curve");
    expect(body.groups).toHaveLength(1);
  });

  it("passes ?era=ALL through to the reader, defaulting to 'current' otherwise", async () => {
    const seenStarts: Array<string | null> = [];
    setMetricsCaller((_db, _schema, start) => {
      seenStarts.push(start);
      return { ok: true, error: null, metrics: { schema: "curve_vx", n_records: 0, groups: {} } };
    });
    await get("/api/performance/curve?era=ALL");
    await get("/api/performance/curve");
    // The fixture's config.json declares a data_epoch, so the two requests must reach the reader
    // with genuinely different bounds -- era=ALL first (no start), the bare route second (bound).
    expect(seenStarts).toEqual([null, "2026-08-21"]);
  });
});
