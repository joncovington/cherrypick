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
import { NO_ATTEMPTS_SCOPE, readEntryAttempts, type AttemptsPayload } from "../src/readers/attempts.js";

/**
 * The attempts page drew every arm of the resolved day: the header's arm/symbol/era controls set the
 * page scope but the route dropped it and the cards never sent it, so the dropdowns moved the
 * session, forest, history and calibration slides while the arm rail and timeline stood still
 * (2026-10-07 -- the forest's own bug, fixed 2026-10-06, on a second surface).
 *
 * Two rules are pinned. The arm and symbol narrow the timeline, the rail and the occupancy legs; and
 * the default session resolves WITHIN the selected era, since no attempts table carries an era
 * column and the day is the only place an era can bite.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "meic-ledger-schema.sql"), "utf-8");

let config: ConsoleConfig;
let app: FastifyInstance;

beforeAll(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-meicattemptsscope-"));
  fs.mkdirSync(path.join(tmp, "meic"), { recursive: true });
  const db = new Database(path.join(tmp, "meic", "paper_trades.db"));
  db.exec(SCHEMA);

  const trade = db.prepare(
    `INSERT INTO ic_trades (trade_date, symbol, ic_order_id, status, arm, era, put_strike, call_strike,
       wing_width, net_credit, quantity, pnl, fees, underlying_price_entry, created_at, updated_at)
     VALUES (?, ?, ?, 'expired', ?, ?, 5950, 6050, 5, 1.2, 1, 100, 5, 6000, ?, ?)`,
  );
  const adv = "2026-09-25 16:00:00-04:00";
  trade.run("2026-09-25", "SPX", "adv-a-spx", "armA", "advisor", adv, adv);
  trade.run("2026-09-25", "NDX", "adv-a-ndx", "armA", "advisor", adv, adv);
  trade.run("2026-09-25", "SPX", "adv-b-spx", "armB", "advisor", adv, adv);
  const smp = "2026-08-13 16:00:00-04:00";
  trade.run("2026-08-13", "SPX", "smp-a-spx", "armA", "sample", smp, smp);

  const attempt = db.prepare(
    `INSERT INTO entry_attempts (ts, trade_date, arm, symbol, outcome, block_detail, put_strike,
       call_strike, wing_width, blocking_strike, seconds_until_cadence_clear, underlying_price)
     VALUES (?, ?, ?, ?, ?, ?, 5950, 6050, 5, NULL, NULL, 6000)`,
  );
  attempt.run("10:00", "2026-09-25", "armA", "SPX", "filled", null);
  attempt.run("10:01", "2026-09-25", "armA", "SPX", "gate_blocked", "regime_gex_negative");
  attempt.run("10:02", "2026-09-25", "armA", "NDX", "sign_rule_blocked", "occupied");
  attempt.run("10:03", "2026-09-25", "armB", "SPX", "gate_blocked", "regime_gex_negative");
  attempt.run("10:00", "2026-08-13", "armA", "SPX", "filled", null);
  db.close();

  config = {
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

const get = (url: string) => app.inject({ method: "GET", url, headers: { host: "127.0.0.1:5070" } });
const armNames = (p: AttemptsPayload) => p.arms.map((a) => a.arm);

describe("the attempts reader honours the page scope", () => {
  it("defaults to the current era's latest session and every arm", () => {
    const p = readEntryAttempts(config, "meic", "paper", null);
    expect(p.tradeDate).toBe("2026-09-25");
    expect(armNames(p)).toEqual(["armA", "armB"]);
    expect(p.timeline).toHaveLength(4);
  });

  it("narrows to the selected arm", () => {
    const p = readEntryAttempts(config, "meic", "paper", null, { ...NO_ATTEMPTS_SCOPE, arm: "armA" });
    expect(armNames(p)).toEqual(["armA"]);
    expect(p.timeline).toHaveLength(3);
    expect(p.timeline.every((r) => r.arm === "armA")).toBe(true);
  });

  it("narrows to the selected symbol", () => {
    const p = readEntryAttempts(config, "meic", "paper", null, { ...NO_ATTEMPTS_SCOPE, symbol: "NDX" });
    expect(p.timeline).toHaveLength(1);
    expect(p.timeline[0]?.symbol).toBe("NDX");
  });
});

describe("the attempts route carries the scope to the reader", () => {
  it("passes arm and symbol through", async () => {
    const res = await get("/api/meic/attempts?mode=paper&arm=armA&symbol=NDX&era=advisor");
    expect(res.statusCode).toBe(200);
    const body = res.json() as AttemptsPayload;
    expect(armNames(body)).toEqual(["armA"]);
    expect(body.timeline).toHaveLength(1);
    expect(body.timeline[0]?.symbol).toBe("NDX");
  });

  it("resolves an older era's own session when only era is chosen", async () => {
    const res = await get("/api/meic/attempts?mode=paper&era=sample");
    expect(res.statusCode).toBe(200);
    const body = res.json() as AttemptsPayload;
    expect(body.tradeDate).toBe("2026-08-13");
    expect(body.timeline).toHaveLength(1);
  });
});
