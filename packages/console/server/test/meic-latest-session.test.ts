import { beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readMeic, readMeicDivergence, readMeicForest, resolveMeicSession, NO_TRADE_QUERY } from "../src/readers/meic.js";
import { readEntryAttempts } from "../src/readers/attempts.js";

/**
 * "Latest session" is the loop's last RUN, not the trade log's last ROW.
 *
 * The 2026-09-30 shape, on meic's own schema: trades on the 29th; on the 30th the loop ran all day
 * and every entry was refused. The page showed the 29th's trades beside the 30th's refusals, and the
 * live page showed a June trade under the same label. Every card must name the 30th, with no trades.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "meic-ledger-schema.sql"), "utf-8");
const TRADED = "2026-09-29";
const REFUSED = "2026-09-30";
let config: ConsoleConfig;

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-meic-session-"));
  fs.mkdirSync(path.join(tmp, "meic"));
  const db = new Database(path.join(tmp, "meic", "paper_trades.db"));
  db.exec(SCHEMA);
  const at = (day: string) => `${day} 10:00:00-04:00`;
  db.prepare(
    `INSERT INTO ic_trades (trade_date, symbol, ic_order_id, status, arm, era, put_strike, call_strike,
       wing_width, net_credit, quantity, pnl, fees, entry_time, created_at, updated_at)
     VALUES (?, 'SPX', 'ic-1', 'expired', 'control', 'advisor', 5950, 6050, 5, 0.8, 1, 80, 6.89, ?, ?, ?)`,
  ).run(TRADED, at(TRADED), at(TRADED), at(TRADED));
  for (const day of [TRADED, REFUSED]) {
    db.prepare("INSERT INTO daily_summary (summary_date, created_at, updated_at) VALUES (?, ?, ?)").run(day, at(day), at(day));
    db.prepare("INSERT INTO loop_log (loop_time, loop_date, created_at) VALUES (?, ?, ?)").run(at(day), day, at(day));
  }
  db.prepare(
    "INSERT INTO entry_attempts (ts, trade_date, arm, symbol, outcome, block_detail) VALUES ('10:00', ?, 'control', 'SPX', 'gate_blocked', 'call_otm_below_floor')",
  ).run(REFUSED);
  db.close();
  config = { paths: { meicDir: path.join(tmp, "meic") } } as unknown as ConsoleConfig;
});

describe("MEIC's latest session", () => {
  it("is the loop's last session, even one with no trades", () => {
    expect(resolveMeicSession(config, "paper")).toBe(REFUSED);
  });

  it("scopes the trade log to that session, and names it", () => {
    const p = readMeic(config, "paper", { ...NO_TRADE_QUERY, era: "ALL" });
    expect(p.session).toBe(REFUSED);
    expect(p.trades.total).toBe(0);
    expect(p.totals.trades).toBe(0);
  });

  it("is the day the forest and the divergence card name too", () => {
    expect(readMeicForest(config, "paper", null).tradeDate).toBe(REFUSED);
    expect(readMeicDivergence(config, "paper", null).date).toBe(REFUSED);
  });

  it("still honours an explicit day", () => {
    const p = readMeic(config, "paper", { ...NO_TRADE_QUERY, era: "ALL", day: TRADED });
    expect(p.session).toBe(TRADED);
    expect(p.trades.total).toBe(1);
  });

  it("is what the attempts route passes the shared reader", () => {
    const attempts = readEntryAttempts(config, "meic", "paper", resolveMeicSession(config, "paper"));
    expect(attempts.tradeDate).toBe(REFUSED);
  });

  it("is null with no ledger, never an invented day", () => {
    expect(resolveMeicSession(config, "live")).toBeNull();
  });
});

/**
 * A closed era ends at its own last trade. The book era's date span runs to 2026-08-06 and the loop
 * journaled 08-05 and 08-06 with every arm blocked, so era=book with no day named 08-06 and showed
 * 0 trades, while a range over the era showed all 290.
 */
describe("a closed era's latest session", () => {
  let bookConfig: ConsoleConfig;
  const LAST_BOOK_TRADE = "2026-08-04";
  const BOOK_TAIL = ["2026-08-05", "2026-08-06"];

  beforeAll(() => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-meic-book-"));
    fs.mkdirSync(path.join(tmp, "meic"));
    const db = new Database(path.join(tmp, "meic", "paper_trades.db"));
    db.exec(SCHEMA);
    const at = (day: string) => `${day} 10:00:00-04:00`;
    const trade = db.prepare(
      `INSERT INTO ic_trades (trade_date, symbol, ic_order_id, status, arm, era, put_strike, call_strike,
         wing_width, net_credit, quantity, pnl, fees, entry_time, created_at, updated_at)
       VALUES (?, 'SPX', ?, 'expired', 'control', ?, 5950, 6050, 5, 0.8, 1, 80, 6.89, ?, ?, ?)`,
    );
    trade.run(LAST_BOOK_TRADE, "ic-book", "book", at(LAST_BOOK_TRADE), at(LAST_BOOK_TRADE), at(LAST_BOOK_TRADE));
    trade.run("2026-09-29", "ic-adv", "advisor", at("2026-09-29"), at("2026-09-29"), at("2026-09-29"));
    for (const day of [LAST_BOOK_TRADE, ...BOOK_TAIL, "2026-09-29", "2026-09-30"]) {
      db.prepare("INSERT INTO loop_log (loop_time, loop_date, created_at) VALUES (?, ?, ?)").run(at(day), day, at(day));
    }
    db.close();
    bookConfig = { paths: { meicDir: path.join(tmp, "meic") } } as unknown as ConsoleConfig;
  });

  it("is its last stamped trade, not the no-trade tail its date span reaches", () => {
    expect(resolveMeicSession(bookConfig, "paper", "book")).toBe(LAST_BOOK_TRADE);
    const p = readMeic(bookConfig, "paper", { ...NO_TRADE_QUERY, era: "book" });
    expect(p.session).toBe(LAST_BOOK_TRADE);
    expect(p.trades.total).toBe(1);
  });

  it("leaves the open era on the journals' day, refused or not", () => {
    expect(resolveMeicSession(bookConfig, "paper", "advisor")).toBe("2026-09-30");
    expect(resolveMeicSession(bookConfig, "paper", "ALL")).toBe("2026-09-30");
  });
});
