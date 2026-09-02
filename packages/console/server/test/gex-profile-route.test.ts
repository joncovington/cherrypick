import { describe, it, expect, beforeAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import Fastify, { type FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../src/config.js";
import { registerSecurity } from "../src/security.js";
import { registerModuleRoutes } from "../src/routes/modules.js";

/**
 * `/api/gex/profile/:symbol` took the symbol straight off the URL. That matters because
 * `buildGexProfile` deliberately falls back to the most recent PAST expiration -- so a weekend
 * still shows Friday's profile -- and the SHARED stream cache holds chains for underlyings every
 * other module streamed and the suite has since retired. Asking for one computed a gamma profile
 * off a five-week-old chain and returned it as a claim about now, which is the one thing a GEX
 * read must never be.
 */

let app: FastifyInstance;

beforeAll(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-gexroute-"));
  fs.mkdirSync(path.join(tmp, "gex"), { recursive: true });

  // The recorder writes SPX alone on its latest session; QQQ is retired history.
  const hist = new Database(path.join(tmp, "gex", "gex_history.db"));
  hist.exec(
    "CREATE TABLE gex_regime_history (symbol TEXT, trade_date TEXT, ts TEXT, spot REAL, net_gex REAL," +
      " net_gex_vol REAL, zero_gamma REAL, call_wall REAL, put_wall REAL, expiration TEXT);",
  );
  const r = hist.prepare("INSERT INTO gex_regime_history (symbol, trade_date, ts, spot) VALUES (?,?,?,?)");
  r.run("SPX", "2026-09-02", new Date().toISOString(), 7631);
  r.run("QQQ", "2026-07-29", "2026-07-29T16:00:00.000Z", 500);
  hist.close();

  // ...but the shared cache still holds a chain for the retired symbol, and for another module's.
  const cache = new Database(path.join(tmp, "s.db"));
  cache.exec(
    "CREATE TABLE stream_chain (streamer_symbol TEXT, expiration TEXT, underlying_symbol TEXT," +
      " data_json TEXT, updated_at REAL);" +
      "CREATE TABLE stream_trades (symbol TEXT, last REAL, volume REAL, updated_at REAL);" +
      "CREATE TABLE stream_greeks (symbol TEXT, gamma REAL, iv REAL);" +
      "CREATE TABLE stream_oi (symbol TEXT, open_interest REAL);",
  );
  const ch = cache.prepare(
    "INSERT INTO stream_chain (streamer_symbol, expiration, underlying_symbol) VALUES (?,?,?)",
  );
  for (const u of ["SPX", "QQQ", "NDX"]) ch.run(`.${u}x`, "2026-07-31", u);
  cache.prepare("INSERT INTO stream_trades (symbol, last, updated_at) VALUES (?,?,?)").run("QQQ", 500, 1);
  cache.close();

  const config = {
    port: 0,
    paths: {
      cherrypick: tmp, streamCacheDb: path.join(tmp, "s.db"), watchdogLast: path.join(tmp, "w.json"),
      orchestratorConfig: path.join(tmp, "c.json"), consoleData: path.join(tmp, "console"),
      meicDir: path.join(tmp, "meic"), fliesDir: path.join(tmp, "flies"),
      earningsDir: path.join(tmp, "earnings"), gexDir: path.join(tmp, "gex"),
      reviewDir: path.join(tmp, "review"), overviewDir: path.join(tmp, "overview"),
      advisorDir: path.join(tmp, "advisor"), adviceDir: path.join(tmp, "advice"),
      meicRiskConfig: path.join(tmp, "r.json"), fliesConfig: path.join(tmp, "f.json"),
    },
  } as unknown as ConsoleConfig;

  app = Fastify();
  registerSecurity(app);
  registerModuleRoutes(app, config);
  await app.ready();
});

const get = (url: string) => app.inject({ method: "GET", url, headers: { host: "127.0.0.1:5070" } });

describe("the GEX profile route", () => {
  it("refuses a symbol the recorder is not writing, however cached its chain", async () => {
    const res = await get("/api/gex/profile/QQQ");
    expect(res.statusCode).toBe(404);
    expect(res.json().error).toContain("QQQ");
  });

  it("refuses a symbol no GEX history has ever mentioned", async () => {
    expect((await get("/api/gex/profile/NDX")).statusCode).toBe(404);
  });

  it("still serves the symbol the recorder writes", async () => {
    const res = await get("/api/gex/profile/SPX");
    expect(res.statusCode).toBe(200);
    // No chain rows for a live expiration here, so the profile itself reports its own failure --
    // what matters is that the route ADMITTED it rather than 404ing the one real symbol.
    expect(res.json()).toHaveProperty("ok");
  });
});
