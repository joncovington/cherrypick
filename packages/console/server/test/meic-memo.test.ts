import { describe, it, expect, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { loadConfig } from "../src/config.js";
import { closePooledDbs } from "../src/readers/db.js";
import { readMeicDeepAnalytics, readMeicPerformance, readMeicScope, readMeicSummary } from "../src/readers/meic.js";

/**
 * MEIC's page-wide reads are memoised on the ledger's stamp (each was 0.8-5 s of the event loop on
 * every poll, 2026-11-02). These pin that a memo never answers for a ledger that has moved, and never
 * answers one scope's question with another's.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "meic-ledger-schema.sql"), "utf-8");
const ALL = { symbol: null, profile: null, era: "ALL" };
const DAY = "2026-10-14";

function ledger() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "console-meic-memo-"));
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(SCHEMA);
  const base = loadConfig();
  return { db, config: { ...base, paths: { ...base.paths, meicDir: dir } } };
}

function trade(db: Database.Database, id: string, symbol: string, pnl: number): void {
  db.prepare(
    `INSERT INTO ic_trades (ic_order_id, trade_date, arm, symbol, status, pnl, fees, net_credit, quantity,
       wing_width, entry_time, call_delta_at_entry, created_at, updated_at)
     VALUES (?, ?, 'control', ?, 'expired', ?, 1, 1.0, 1, 5, ?, 0.1, ?, ?)`,
  ).run(id, DAY, symbol, pnl, `${DAY}T12:00:00`, `${DAY}T12:00:00`, `${DAY}T16:00:00`);
}

afterEach(() => closePooledDbs());

describe("MEIC's memoised page reads", () => {
  it("answer again once the ledger is written", () => {
    const { db, config } = ledger();
    trade(db, "t1", "SPX", 10);
    const trades = () => readMeicPerformance(config, "paper", "daily", null, null, "ALL").arms.reduce((s, a) => s + a.trades, 0);
    expect(readMeicScope(config, "paper", "ALL").symbols).toEqual(["SPX"]);
    expect(trades()).toBe(1);
    expect(readMeicSummary(config, "paper", ALL, "all", DAY, undefined).hero.trades).toBe(1);
    expect(readMeicDeepAnalytics(config, "paper", ALL).bySymbol.map((r) => r.bucket)).toEqual(["SPX"]);

    trade(db, "t2", "XSP", 20);
    expect(readMeicScope(config, "paper", "ALL").symbols).toEqual(["SPX", "XSP"]);
    expect(trades()).toBe(2);
    expect(readMeicSummary(config, "paper", ALL, "all", DAY, undefined).hero.trades).toBe(2);
    expect(readMeicDeepAnalytics(config, "paper", ALL).bySymbol.map((r) => r.bucket).sort()).toEqual(["SPX", "XSP"]);
    db.close();
  });

  it("keep each scope's answer apart on an unchanged ledger", () => {
    const { db, config } = ledger();
    trade(db, "t1", "SPX", 10);
    trade(db, "t2", "XSP", 20);
    const perf = (symbol: string | null) =>
      readMeicPerformance(config, "paper", "daily", symbol, null, "ALL").arms.reduce((s, a) => s + a.trades, 0);
    expect(perf(null)).toBe(2);
    expect(perf("SPX")).toBe(1);
    const summary = (symbol: string | null) => readMeicSummary(config, "paper", { ...ALL, symbol }, "all", DAY, undefined).hero.trades;
    expect(summary(null)).toBe(2);
    expect(summary("XSP")).toBe(1);
    const deep = (symbol: string | null) => readMeicDeepAnalytics(config, "paper", { ...ALL, symbol }).bySymbol.length;
    expect(deep(null)).toBe(2);
    expect(deep("SPX")).toBe(1);
    db.close();
  });
});
