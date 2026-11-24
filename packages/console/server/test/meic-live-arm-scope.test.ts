import { describe, it, expect } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readMeicScope, readMeic, NO_TRADE_QUERY } from "../src/readers/meic.js";

/**
 * The live ledger tags every trade `arm = 'live'` (the account); the strategy arm is on the trade's filled
 * entry attempt. Live scope lists, filters and counts must therefore go through that join, or the
 * page offers one arm called "live" and a filter on a real arm selects nothing.
 */

function home(live: boolean): { cfg: ConsoleConfig; db: Database.Database } {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "meic-live-scope-"));
  const meic = path.join(root, "data", "meic");
  fs.mkdirSync(meic, { recursive: true });
  fs.mkdirSync(path.join(root, "config"), { recursive: true });
  fs.writeFileSync(path.join(root, "config", "meic.json"), JSON.stringify({ live: { arm: "pm-200", symbol: "SPX" } }));
  const db = new Database(path.join(meic, live ? "meic_trades.db" : "paper_trades.db"));
  db.exec(
    `CREATE TABLE ic_trades (id INTEGER PRIMARY KEY, ic_order_id TEXT, trade_date TEXT, symbol TEXT, arm TEXT, era TEXT,
       status TEXT, pnl REAL, fees REAL, net_credit REAL, quantity INTEGER, exit_reason TEXT, entry_time TEXT, put_strike REAL, call_strike REAL, wing_width REAL, put_credit REAL, call_credit REAL, iv_rank_at_entry REAL);
     CREATE TABLE entry_attempts (id INTEGER PRIMARY KEY, ic_order_id TEXT, arm TEXT, outcome TEXT)`,
  );
  const trade = db.prepare("INSERT INTO ic_trades (ic_order_id, trade_date, symbol, arm, era, status, pnl, fees, net_credit, quantity, entry_time, exit_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)");
  const attempt = db.prepare("INSERT INTO entry_attempts (ic_order_id, arm, outcome) VALUES (?,?,?)");
  const tag = live ? "live" : null;
  const rows: Array<[string, string, string, string, number]> = [
    ["o1", "2026-11-02", "SPX", "pm-200", 20],
    ["o2", "2026-11-03", "SPX", "pm-200", -50],
    ["o3", "2026-11-03", "SPX", "eq-hourly", 30],
    ["o4", "2026-11-04", "XSP", "eq-hourly", 10],
  ];
  for (const [id, d, sym, arm, pnl] of rows) {
    trade.run(id, d, sym, tag ?? arm, "current", "closed", pnl, 1, 1, 1, "10:00", "expired");
    attempt.run(id, arm, "filled");
    attempt.run(id, "other", "rejected");
  }
  return { cfg: { paths: { cherrypick: root, meicDir: meic } } as unknown as ConsoleConfig, db };
}

describe("live MEIC scope reads the registry arm", () => {
  it("lists the strategy arms with trades and sessions, and the configured live defaults", () => {
    const { cfg, db } = home(true);
    db.close();
    const s = readMeicScope(cfg, "live", "ALL");
    expect(s.arms).toEqual(["eq-hourly", "pm-200"]);
    expect(s.liveArm).toBe("pm-200");
    expect(s.liveSymbol).toBe("SPX");
    expect(s.armStats).toEqual([
      { arm: "eq-hourly", trades: 2, sessions: 2 },
      { arm: "pm-200", trades: 2, sessions: 2 },
    ]);
    expect(s.symbolStats.find((x) => x.symbol === "SPX")).toEqual({ symbol: "SPX", trades: 3, sessions: 2 });
  });

  it("filters trades through the same join, never the live tag", () => {
    const { cfg, db } = home(true);
    db.close();
    const one = readMeic(cfg, "live", { ...NO_TRADE_QUERY, from: "2026-01-01", to: "2026-12-31", profile: "pm-200", era: "ALL" });
    const all = readMeic(cfg, "live", { ...NO_TRADE_QUERY, from: "2026-01-01", to: "2026-12-31", profile: null, era: "ALL" });
    expect(one.trades.total).toBe(2);
    expect(all.trades.total).toBe(4);
    // the account tag is not an arm
    const tag = readMeic(cfg, "live", { ...NO_TRADE_QUERY, from: "2026-01-01", to: "2026-12-31", profile: "live", era: "ALL" });
    expect(tag.trades.total).toBe(0);
  });

  it("leaves paper on its own arm column, with no live fields", () => {
    const { cfg, db } = home(false);
    db.close();
    const s = readMeicScope(cfg, "paper", "ALL");
    expect(s.arms).toEqual(["eq-hourly", "pm-200"]);
    expect(s.liveArm).toBeNull();
    expect(s.armStats).toEqual([]);
  });
});
