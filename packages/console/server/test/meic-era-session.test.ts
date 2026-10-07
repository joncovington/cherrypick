import { afterAll, beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import Fastify, { type FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../src/config.js";
import { registerSecurity } from "../src/security.js";
import { registerModuleRoutes } from "../src/routes/modules.js";
import { closePooledDbs } from "../src/readers/db.js";

/**
 * Under an older era with no session picked, the forest and the trade log resolve that era's own
 * latest day, but the attempts, occupancy and divergence routes resolved the current era's -- so
 * one lightbox named two days (2026-10-07). Every session-resolved MEIC route takes the era.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "meic-ledger-schema.sql"), "utf-8");
const ADVISOR_DAY = "2026-09-25";
const SAMPLE_DAY = "2026-08-13";

let app: FastifyInstance;

beforeAll(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-meic-era-session-"));
  fs.mkdirSync(path.join(tmp, "meic"), { recursive: true });
  const db = new Database(path.join(tmp, "meic", "paper_trades.db"));
  db.exec(SCHEMA);
  const ins = db.prepare(
    `INSERT INTO ic_trades (trade_date, symbol, ic_order_id, status, arm, era, put_strike, call_strike,
       wing_width, net_credit, quantity, pnl, fees, underlying_price_entry, created_at, updated_at)
     VALUES (?, 'SPX', ?, 'expired', 'armA', ?, 5950, 6050, 5, 1.2, 1, 100, 5, 6000, ?, ?)`,
  );
  const att = db.prepare(
    "INSERT INTO entry_attempts (ts, trade_date, arm, symbol, outcome) VALUES ('10:00', ?, 'armA', 'SPX', 'filled')",
  );
  for (const [day, era] of [
    [ADVISOR_DAY, "advisor"],
    [SAMPLE_DAY, "sample"],
  ] as const) {
    const at = `${day} 16:00:00-04:00`;
    ins.run(day, `ic-${era}`, era, at, at);
    att.run(day);
  }
  db.close();

  const config = {
    port: 0,
    paths: {
      cherrypick: tmp,
      streamCacheDb: path.join(tmp, "s.db"),
      watchdogLast: path.join(tmp, "w.json"),
      orchestratorConfig: path.join(tmp, "c.json"),
      consoleData: path.join(tmp, "console"),
      meicDir: path.join(tmp, "meic"),
      fliesDir: path.join(tmp, "flies"),
      earningsDir: path.join(tmp, "earnings"),
      gexDir: path.join(tmp, "gex"),
      reviewDir: path.join(tmp, "review"),
      overviewDir: path.join(tmp, "overview"),
      advisorDir: path.join(tmp, "advisor"),
      adviceDir: path.join(tmp, "advice"),
      meicRiskConfig: path.join(tmp, "r.json"),
      fliesConfig: path.join(tmp, "f.json"),
    },
  } as unknown as ConsoleConfig;

  app = Fastify();
  registerSecurity(app);
  registerModuleRoutes(app, config);
  await app.ready();
});

afterAll(async () => {
  await app.close();
  closePooledDbs();
});

const get = async (url: string) => {
  const res = await app.inject({ method: "GET", url, headers: { host: "127.0.0.1:5070" } });
  expect(res.statusCode).toBe(200);
  return res.json() as Record<string, unknown>;
};

describe("session-resolved MEIC routes resolve within the page's era", () => {
  it.each([
    ["attempts", "tradeDate"],
    ["occupancy", "tradeDate"],
    ["divergence", "date"],
  ])("/api/meic/%s names the selected era's latest session", async (route, field) => {
    expect((await get(`/api/meic/${route}?mode=paper&era=sample`))[field]).toBe(SAMPLE_DAY);
  });

  it.each([
    ["attempts", "tradeDate"],
    ["occupancy", "tradeDate"],
    ["divergence", "date"],
  ])("/api/meic/%s still names the current era's session with no era", async (route, field) => {
    expect((await get(`/api/meic/${route}?mode=paper`))[field]).toBe(ADVISOR_DAY);
  });

  it("an explicit date still wins over the era", async () => {
    expect((await get(`/api/meic/attempts?mode=paper&era=sample&date=${ADVISOR_DAY}`))["tradeDate"]).toBe(ADVISOR_DAY);
  });
});
