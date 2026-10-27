import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import Fastify, { type FastifyInstance } from "fastify";
import { registerBacktestRoutes } from "../src/routes/backtest.js";
import { setBridgeCaller, type BridgeResult } from "../src/services/configBridge.js";
import { invalidateFeatures } from "../src/services/featuresBridge.js";
import type { ConsoleConfig } from "../src/config.js";

/**
 * A queue task's log tail, for its expanded row (2026-10-27). Behind the Backtest gate like every
 * route on the page, and the path is built from an integer id alone: nothing outside
 * `logs/research-queue/` can be named.
 */

let app: FastifyInstance;
let home: string;

function features(on: boolean): BridgeResult {
  return { ok: true, capabilities: {}, modules: {}, services: {}, features: { backtest: on } } as unknown as BridgeResult;
}

beforeAll(async () => {
  home = fs.mkdtempSync(path.join(os.tmpdir(), "queue-log-"));
  fs.mkdirSync(path.join(home, "logs", "research-queue"), { recursive: true });
  const lines = Array.from({ length: 60 }, (_, i) => `line ${String(i + 1)}`);
  fs.writeFileSync(path.join(home, "logs", "research-queue", "3.log"), `${lines.join("\n")}\n`);
  fs.writeFileSync(path.join(home, "logs", "secret.log"), "not for the queue tab\n");
  app = Fastify();
  registerBacktestRoutes(app, { paths: { cherrypick: home } } as unknown as ConsoleConfig);
  await app.ready();
});

afterAll(async () => {
  await app.close();
  fs.rmSync(home, { recursive: true, force: true });
});

afterEach(() => {
  setBridgeCaller();
  invalidateFeatures();
});

describe("a queue task's log tail", () => {
  it("returns the last 40 lines of that task's log", async () => {
    setBridgeCaller(() => features(true));
    const r = await app.inject({ method: "GET", url: "/api/backtest/queue/3/log" });
    expect(r.statusCode).toBe(200);
    const lines = (r.json() as { data: { lines: string[] } }).data.lines;
    expect(lines).toHaveLength(40);
    expect(lines[0]).toBe("line 21");
    expect(lines.at(-1)).toBe("line 60");
  });

  it("is empty for a task with no log yet", async () => {
    setBridgeCaller(() => features(true));
    const r = await app.inject({ method: "GET", url: "/api/backtest/queue/99/log" });
    expect((r.json() as { data: { lines: string[] } }).data.lines).toEqual([]);
  });

  it("refuses anything but a positive integer id", async () => {
    setBridgeCaller(() => features(true));
    for (const bad of ["0", "-1", "3.5", "..%2Fsecret", "abc", "1e3"]) {
      const r = await app.inject({ method: "GET", url: `/api/backtest/queue/${bad}/log` });
      expect([400, 404]).toContain(r.statusCode);
      expect(r.body).not.toContain("not for the queue tab");
    }
  });

  it("is closed with the Backtest gate", async () => {
    setBridgeCaller(() => features(false));
    const r = await app.inject({ method: "GET", url: "/api/backtest/queue/3/log" });
    expect(r.statusCode).toBe(404);
  });
});
