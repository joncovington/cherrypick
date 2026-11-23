import { describe, it, expect, beforeAll, afterAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Fastify, { type FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../src/config.js";
import { registerSecurity } from "../src/security.js";
import { registerModuleRoutes } from "../src/routes/modules.js";
import { readDecisions } from "../src/readers/decisions.js";

/**
 * The module routes' shared plumbing: the six paper modules' decision journals answer from one reader
 * with no date, and the routes that take an automation's name or a session answer a malformed one with
 * the same 400 and message. Pinned so folding the repeated routes cannot change what a page receives.
 */
let app: FastifyInstance;
let tmp: string;
let config: ConsoleConfig;

beforeAll(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-modroutes-"));
  fs.writeFileSync(path.join(tmp, "config.json"), JSON.stringify({}));
  const dirs = Object.fromEntries(
    ["meic", "flies", "earnings", "calendars", "pmcc", "curve", "bwb", "contango", "weeklyput", "ladder", "automations", "gex"].map((m) => [
      `${m}Dir`,
      path.join(tmp, m),
    ]),
  );
  config = {
    port: 0,
    paths: {
      cherrypick: tmp,
      streamCacheDb: "",
      watchdogLast: "",
      orchestratorConfig: path.join(tmp, "config.json"),
      consoleData: "",
      ...dirs,
      reviewDir: "",
      manageDir: "",
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
  registerModuleRoutes(app, config);
  await app.ready();
});

afterAll(async () => {
  await app.close();
  fs.rmSync(tmp, { recursive: true, force: true });
});

describe("decision journal routes", () => {
  for (const module of ["curve", "pmcc", "contango", "weeklyput", "ladder", "automations"] as const) {
    it(`/api/${module}/decisions is the reader's answer for ${module}, with no date`, async () => {
      const res = await app.inject({ method: "GET", url: `/api/${module}/decisions` });
      expect(res.statusCode).toBe(200);
      expect(res.json()).toEqual(JSON.parse(JSON.stringify(readDecisions(config, module, null))));
    });
  }

  it("bwb's keeps its mode", async () => {
    const res = await app.inject({ method: "GET", url: "/api/bwb/decisions?mode=live" });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toEqual(JSON.parse(JSON.stringify(readDecisions(config, "bwb", null, "live"))));
  });
});

describe("a malformed automation name", () => {
  for (const route of ["reading", "entries", "conditions"]) {
    for (const name of ["", "A", "x", "has space", "-lead", "a".repeat(60)]) {
      it(`/api/automations/automation/${route}?name=${JSON.stringify(name)} is a 400 naming the field`, async () => {
        const res = await app.inject({ method: "GET", url: `/api/automations/automation/${route}?name=${encodeURIComponent(name)}` });
        expect(res.statusCode).toBe(400);
        expect(res.json()).toEqual({ error: "name: an automation's name" });
      });
    }
    it(`/api/automations/automation/${route} with no name is a 400`, async () => {
      const res = await app.inject({ method: "GET", url: `/api/automations/automation/${route}` });
      expect(res.statusCode).toBe(400);
    });
  }
});

describe("a malformed session", () => {
  for (const route of ["/api/flies/opening-range", "/api/flies/regime-cuts", "/api/meic/regime-cuts"]) {
    it(`${route}?session=yesterday is a 400`, async () => {
      const res = await app.inject({ method: "GET", url: `${route}?session=yesterday` });
      expect(res.statusCode).toBe(400);
      expect(res.json()).toEqual({ error: "session must be YYYY-MM-DD" });
    });
  }
});
