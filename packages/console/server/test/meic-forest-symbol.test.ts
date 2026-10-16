import { describe, it, expect, beforeAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { forestGrid, readMeicForest } from "../src/readers/meic.js";

/**
 * One forest is one price axis. A day with SPX (~7700) and XSP (~770) condors drawn together
 * stretched the axis from ~600 to ~8400 and priced every XSP condor at SPX prices (2026-10-15).
 * The grid is priced at the strikes' own step, so a 5-wide SPX condor's kinks are grid points.
 */

let config: ConsoleConfig;
const DAY = "2026-08-13";

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-meicforest-sym-"));
  const dir = path.join(tmp, "meic");
  fs.mkdirSync(dir, { recursive: true });
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(`
    CREATE TABLE ic_trades (
      id INTEGER PRIMARY KEY, ic_order_id TEXT, trade_date TEXT, risk_profile TEXT, symbol TEXT,
      put_strike REAL, call_strike REAL, wing_width REAL, net_credit REAL, quantity INTEGER,
      status TEXT, underlying_price_entry REAL, pnl REAL, fees REAL
    );
  `);
  const ins = db.prepare(
    `INSERT INTO ic_trades (ic_order_id, trade_date, risk_profile, symbol, put_strike, call_strike,
                            wing_width, net_credit, quantity, status, underlying_price_entry, pnl, fees)
     VALUES (?, ?, ?, ?, ?, ?, ?, 1.0, 1, 'expired', ?, 50, 1)`,
  );
  for (let i = 0; i < 3; i += 1) ins.run(`s${i}`, DAY, "control", "SPX", 7700 - i * 5, 7760 + i * 5, 5, 7744);
  ins.run("x1", DAY, "live-shadow", "XSP", 770, 775, 2, 774.4);
  db.close();
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

const ALL = { symbol: null, profile: null, era: "ALL" };

describe("the MEIC forest draws one symbol", () => {
  it("defaults to the symbol with the most condors and lists the day's symbols", () => {
    const f = readMeicForest(config, "paper", DAY, ALL);
    expect(f.symbol).toBe("SPX");
    expect(f.symbols).toEqual(["SPX", "XSP"]);
    expect(f.asEntered.map((a) => a.arm)).toEqual(["control"]);
    const grid = f.asEntered[0]!.prices;
    expect(Math.min(...grid)).toBeGreaterThan(7000); // never stretched down to XSP's ~770
  });

  it("draws the other symbol on request, and the page's own symbol always wins", () => {
    expect(readMeicForest(config, "paper", DAY, ALL, "XSP").asEntered.map((a) => a.arm)).toEqual(["live-shadow"]);
    expect(readMeicForest(config, "paper", DAY, { ...ALL, symbol: "SPX" }, "XSP").symbol).toBe("SPX");
  });

  it("prices the grid at the strikes' own step", () => {
    const g = forestGrid([7690, 7695, 7700, 7760, 7765, 7770]);
    const steps = new Set(g.slice(1).map((v, i) => Math.round((v - g[i]!) * 100) / 100));
    expect(steps).toEqual(new Set([5]));
    expect(g).toContain(7690);
    expect(g).toContain(7770);
  });
});
