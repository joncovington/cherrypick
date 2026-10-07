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
import { NO_SCOPE, readMeicForest, type MeicForest } from "../src/readers/meic.js";

/**
 * The forest drew every arm and every symbol of the resolved day: the header's arm/symbol/era
 * controls set the page scope but the route dropped it and the card never sent it, so the dropdowns
 * moved the session, history and calibration slides while the forest stood still (2026-10-06).
 *
 * Two rules are pinned here. The scope narrows the curves, and the default session is resolved
 * WITHIN the selected era -- without the second, choosing an older era with no session picked named
 * the current era's latest day, which has no rows there, and drew an empty card.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "meic-ledger-schema.sql"), "utf-8");

let config: ConsoleConfig;
let app: FastifyInstance;

beforeAll(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-meicforestscope-"));
  fs.mkdirSync(path.join(tmp, "meic"), { recursive: true });
  const db = new Database(path.join(tmp, "meic", "paper_trades.db"));
  db.exec(SCHEMA);
  const ins = db.prepare(
    `INSERT INTO ic_trades (trade_date, symbol, ic_order_id, status, arm, era, put_strike, call_strike,
       wing_width, net_credit, quantity, pnl, fees, underlying_price_entry, created_at, updated_at)
     VALUES (?, ?, ?, 'expired', ?, ?, 5950, 6050, 5, 1.2, 1, 100, 5, 6000, ?, ?)`,
  );
  const adv = "2026-09-25 16:00:00-04:00";
  ins.run("2026-09-25", "SPX", "adv-a-spx", "armA", "advisor", adv, adv);
  ins.run("2026-09-25", "NDX", "adv-a-ndx", "armA", "advisor", adv, adv);
  ins.run("2026-09-25", "SPX", "adv-b-spx", "armB", "advisor", adv, adv);
  const smp = "2026-08-13 16:00:00-04:00";
  ins.run("2026-08-13", "SPX", "smp-a-spx", "armA", "sample", smp, smp);
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
const profiles = (f: MeicForest) => f.asEntered.map((a) => a.profile);

describe("the forest honours the page scope", () => {
  it("defaults to the current era's latest session", () => {
    const f = readMeicForest(config, "paper", null);
    expect(f.tradeDate).toBe("2026-09-25");
    expect(profiles(f)).toEqual(["armA", "armB"]);
  });

  it("narrows to the selected arm", () => {
    const f = readMeicForest(config, "paper", null, { ...NO_SCOPE, profile: "armA" });
    expect(profiles(f)).toEqual(["armA"]);
    expect(f.tradesToday).toBe(2);
  });

  it("narrows to the selected symbol", () => {
    const f = readMeicForest(config, "paper", null, { ...NO_SCOPE, symbol: "NDX" });
    expect(profiles(f)).toEqual(["armA"]);
    expect(f.tradesToday).toBe(1);
  });

  it("resolves the default session WITHIN the selected era", () => {
    // Before this, era=sample with no session picked named 2026-09-25 (the advisor day), which has
    // no sample rows, and drew an empty forest.
    const f = readMeicForest(config, "paper", null, { ...NO_SCOPE, era: "sample" });
    expect(f.tradeDate).toBe("2026-08-13");
    expect(profiles(f)).toEqual(["armA"]);
    expect(f.tradesToday).toBe(1);
  });
});

describe("the forest route carries the scope to the reader", () => {
  it("passes profile and symbol through", async () => {
    const res = await get("/api/meic/forest?mode=paper&profile=armA&symbol=NDX&era=advisor");
    expect(res.statusCode).toBe(200);
    const body = res.json() as MeicForest;
    expect(profiles(body)).toEqual(["armA"]);
    expect(body.tradesToday).toBe(1);
  });

  it("resolves an older era's own session when only era is chosen", async () => {
    const res = await get("/api/meic/forest?mode=paper&era=sample");
    expect(res.statusCode).toBe(200);
    expect((res.json() as MeicForest).tradeDate).toBe("2026-08-13");
  });
});
