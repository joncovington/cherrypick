import { describe, it, expect, beforeAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readMeicForest } from "../src/readers/meic.js";

/**
 * The forest's spot marker used to be the last ENTRY's spot, so on 2026-10-15 it froze at the day's
 * final entry while the card refetched every 30 seconds. These pin where it comes from now: the
 * stream cache on today's session (age-gated), the settlement print on a past one, and the last
 * entry only when neither exists.
 */

let tmp: string;
let config: ConsoleConfig;
const TODAY = new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
const PAST = "2026-08-13";

function secondsSinceEtMidnight(): number {
  const et = new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
  return et.getHours() * 3600 + et.getMinutes() * 60 + et.getSeconds();
}

function writeCache(lastSpx: number | null, ageS: number) {
  const p = path.join(tmp, "stream_cache.db");
  fs.rmSync(p, { force: true });
  const db = new Database(p);
  db.exec(`
    CREATE TABLE stream_quotes (symbol TEXT PRIMARY KEY, bid REAL, ask REAL, updated_at REAL);
    CREATE TABLE stream_trades (symbol TEXT PRIMARY KEY, last REAL, updated_at REAL);
  `);
  if (lastSpx !== null) {
    db.prepare("INSERT INTO stream_trades VALUES ('SPX', ?, ?)").run(lastSpx, Date.now() / 1000 - ageS);
  }
  db.close();
}

beforeAll(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-meicforest-spot-"));
  const dir = path.join(tmp, "meic");
  fs.mkdirSync(dir, { recursive: true });
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(`
    CREATE TABLE ic_trades (
      id INTEGER PRIMARY KEY, ic_order_id TEXT, trade_date TEXT, risk_profile TEXT, symbol TEXT,
      put_strike REAL, call_strike REAL, wing_width REAL, net_credit REAL, quantity INTEGER,
      status TEXT, underlying_price_entry REAL, settle_underlying REAL, pnl REAL, fees REAL
    );
  `);
  const ins = db.prepare(
    `INSERT INTO ic_trades (ic_order_id, trade_date, risk_profile, symbol, put_strike, call_strike,
                            wing_width, net_credit, quantity, status, underlying_price_entry,
                            settle_underlying, pnl, fees)
     VALUES (?, ?, 'control', 'SPX', 7750, 7810, 10, 2.0, 1, ?, ?, ?, NULL, 0)`,
  );
  ins.run("t1", TODAY, "open", 7770, null);
  ins.run("t2", TODAY, "open", 7780, null);
  ins.run("p1", PAST, "expired", 7700, 7712.5);
  db.close();

  // The live ledger: today's book already SETTLED on the official print.
  const live = new Database(path.join(dir, "meic_trades.db"));
  live.exec(`
    CREATE TABLE ic_trades (
      id INTEGER PRIMARY KEY, ic_order_id TEXT, trade_date TEXT, risk_profile TEXT, symbol TEXT,
      put_strike REAL, call_strike REAL, wing_width REAL, net_credit REAL, quantity INTEGER,
      status TEXT, underlying_price_entry REAL, settle_underlying REAL, pnl REAL, fees REAL
    );
  `);
  live
    .prepare(
      `INSERT INTO ic_trades (ic_order_id, trade_date, risk_profile, symbol, put_strike, call_strike,
                              wing_width, net_credit, quantity, status, underlying_price_entry,
                              settle_underlying, pnl, fees)
       VALUES ('l1', ?, 'live', 'SPX', 7700, 7800, 5, 1.0, 1, 'expired', 7752.98, 7744.06, 100, 2)`,
    )
    .run(TODAY);
  live.close();

  config = {
    port: 0,
    paths: {
      cherrypick: tmp,
      streamCacheDb: path.join(tmp, "stream_cache.db"),
      watchdogLast: path.join(tmp, "watchdog.last.json"),
      orchestratorConfig: path.join(tmp, "config.json"),
      consoleData: path.join(tmp, "console"),
      meicDir: dir,
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
});

describe("the MEIC forest's spot marker", () => {
  it("follows the stream cache on today's session, not the last entry", () => {
    writeCache(7791.25, 5);
    expect(readMeicForest(config, "paper", TODAY).lastSpot).toBe(7791.25);
    writeCache(7795.5, 5);
    expect(readMeicForest(config, "paper", TODAY).lastSpot).toBe(7795.5);
  });

  it("after the close keeps today's last cached price, not the last entry's spot", () => {
    // 2026-10-15: past 15 minutes the marker fell back to the last ENTRY (7752.98 against a
    // 7744.06 close). Needs at least ~17 minutes of the ET day behind us to stage "stale but today".
    const since = secondsSinceEtMidnight();
    if (since < 1000) return;
    writeCache(7744.06, Math.min(3600, since - 60));
    expect(readMeicForest(config, "paper", TODAY).lastSpot).toBe(7744.06);
  });

  it("never draws a cached price from before today's session; it falls back to the last entry", () => {
    writeCache(7791.25, secondsSinceEtMidnight() + 3600);
    expect(readMeicForest(config, "paper", TODAY).lastSpot).toBe(7780);
    writeCache(null, 0);
    expect(readMeicForest(config, "paper", TODAY).lastSpot).toBe(7780);
  });

  it("shows today's settlement print once the book has settled", () => {
    writeCache(7791.25, secondsSinceEtMidnight() + 3600);
    expect(readMeicForest(config, "live", TODAY).lastSpot).toBe(7744.06);
  });

  it("shows a past session's settlement print, whatever the cache holds", () => {
    writeCache(7791.25, 5);
    expect(readMeicForest(config, "paper", PAST).lastSpot).toBe(7712.5);
  });
});
