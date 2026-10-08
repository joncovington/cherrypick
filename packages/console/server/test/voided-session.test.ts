import { afterAll, beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readEntryAttempts } from "../src/readers/attempts.js";
import { readFlies } from "../src/readers/flies.js";
import { closePooledDbs } from "../src/readers/db.js";

/**
 * A voided session shows nothing on a read surface (decided 2026-10-08).
 *
 * The power outage of 2026-10-08 left every paper position of the day unmanaged: flies voided its
 * 22 and settled its 15 books only to close them; MEIC cancelled its 184. The positions and
 * occupancy readers already leave voided and cancelled rows out, but the books table listed the 15
 * books (their stored pnl still counts the voided rows) and the attempts timeline drew the fills
 * that became those positions.
 *
 * Each case below is one way the rule could hide too much or too little: the voided day, an
 * ordinary day, a no-trade day (attempts, no positions, no break), and an older partial session
 * whose positions were kept.
 *
 * The schemas are the modules' own: MEIC's generated fixture, and flies' DDL as its db.connect
 * builds it (fixtures/flies-module-schema.sql).
 */

const FIXTURES = path.join(__dirname, "fixtures");
const VOIDED = "2026-10-08";
const NORMAL = "2026-10-07";
const NO_TRADE = "2026-10-06";
const KEPT = "2026-09-30"; // a partial session whose positions were kept

let config: ConsoleConfig;

function seedFlies(dir: string): void {
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(fs.readFileSync(path.join(FIXTURES, "flies-module-schema.sql"), "utf-8"));
  const attempt = db.prepare(
    `INSERT INTO fly_entry_attempts (ts, trade_date, arm, symbol, outcome, center, spot)
     VALUES (?, ?, 'control', 'SPX', ?, 7785, 7786)`,
  );
  const position = db.prepare(
    `INSERT INTO fly_positions (position_id, book_id, trade_date, arm, symbol, kind, side, center,
       wing_width, quantity, net, credit, fees, status, void_reason, entry_time, created_at)
     VALUES (?, ?, ?, 'control', 'SPX', 'fly', 'call', 7785, 5, 1, 0.25, 2.45, 6.89, 'settled', ?, ?, ?)`,
  );
  const book = db.prepare(
    `INSERT INTO fly_books (book_id, trade_date, arm, symbol, credit_collected, debits_paid, fees,
       net_cash, pnl, status, created_at, updated_at)
     VALUES (?, ?, 'control', 'SPX', 245, 0, 6.89, 238.11, 18.11, 'settled', ?, ?)`,
  );
  const brk = db.prepare(
    `INSERT INTO measurement_breaks (break_date, scope, kind, reason, created_at)
     VALUES (?, '*', 'partial_session', 'power outage', ?)`,
  );
  for (const day of [VOIDED, NORMAL, KEPT]) {
    const bid = `${day}:control:SPX`;
    const at = `${day}T10:15:00-04:00`;
    book.run(bid, day, at, at);
    position.run(`p-${day}`, bid, day, day === VOIDED ? "power outage 2026-10-08" : null, at, at);
    attempt.run(at, day, "filled");
  }
  attempt.run(`${NO_TRADE}T10:00:00-04:00`, NO_TRADE, "gate_blocked");
  brk.run(VOIDED, `${VOIDED}T19:30:00-04:00`);
  brk.run(KEPT, `${KEPT}T19:30:00-04:00`);
  db.close();
}

function seedMeic(dir: string): void {
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(fs.readFileSync(path.join(FIXTURES, "meic-ledger-schema.sql"), "utf-8"));
  const arm = db.prepare("PRAGMA table_info(ic_trades)").all().some((c) => (c as { name: string }).name === "arm")
    ? "arm"
    : "risk_profile";
  const trade = db.prepare(
    `INSERT INTO ic_trades (trade_date, symbol, ic_order_id, status, ${arm}, put_strike, call_strike,
       wing_width, net_credit, quantity, created_at, updated_at)
     VALUES (?, 'SPX', ?, ?, 'control', 5950, 6050, 5, 1.2, 1, ?, ?)`,
  );
  const attemptArm = db.prepare("PRAGMA table_info(entry_attempts)").all().some(
    (c) => (c as { name: string }).name === "arm",
  )
    ? "arm"
    : "risk_profile";
  const attempt = db.prepare(
    `INSERT INTO entry_attempts (ts, trade_date, ${attemptArm}, symbol, outcome, put_strike, underlying_price)
     VALUES (?, ?, 'control', 'SPX', ?, 5950, 6000)`,
  );
  const brk = db.prepare(
    `INSERT INTO measurement_breaks (break_date, scope, kind, reason, created_at)
     VALUES (?, '*', 'partial_session', 'power outage', ?)`,
  );
  for (const day of [VOIDED, NORMAL, KEPT]) {
    const at = `${day} 10:15:00-04:00`;
    trade.run(day, `ic-${day}`, day === VOIDED ? "cancelled" : "expired", at, at);
    attempt.run(at, day, "filled");
  }
  attempt.run(`${NO_TRADE} 10:00:00-04:00`, NO_TRADE, "gate_blocked");
  brk.run(VOIDED, `${VOIDED}T19:30:00-04:00`);
  brk.run(KEPT, `${KEPT}T19:30:00-04:00`);
  db.close();
}

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-voided-session-"));
  const fliesDir = path.join(tmp, "flies");
  const meicDir = path.join(tmp, "meic");
  fs.mkdirSync(fliesDir, { recursive: true });
  fs.mkdirSync(meicDir, { recursive: true });
  seedFlies(fliesDir);
  seedMeic(meicDir);
  config = { paths: { fliesDir, meicDir } } as unknown as ConsoleConfig;
});

afterAll(() => {
  closePooledDbs();
});

const fliesBooks = (date: string) =>
  readFlies(config, "paper", { arm: null, date, symbol: null, era: "ALL" }).books.rows.map((b) => b.bookId);

describe("a voided session shows nothing", () => {
  it("lists no flies book for the voided day", () => {
    expect(fliesBooks(VOIDED)).toEqual([]);
  });

  it("draws no attempts timeline for the voided day, in either module", () => {
    for (const module of ["flies", "meic"] as const) {
      const p = readEntryAttempts(config, module, "paper", VOIDED);
      expect(p.tradeDate, module).toBe(VOIDED);
      expect(p.timeline, module).toEqual([]);
      expect(p.arms, module).toEqual([]);
    }
  });
});

describe("and nothing else is hidden", () => {
  it("still lists an ordinary day's book and attempts", () => {
    expect(fliesBooks(NORMAL)).toEqual([`${NORMAL}:control:SPX`]);
    for (const module of ["flies", "meic"] as const) {
      expect(readEntryAttempts(config, module, "paper", NORMAL).timeline, module).toHaveLength(1);
    }
  });

  it("still draws a no-trade day's attempts (no positions, no break)", () => {
    for (const module of ["flies", "meic"] as const) {
      expect(readEntryAttempts(config, module, "paper", NO_TRADE).timeline, module).toHaveLength(1);
    }
  });

  it("still shows a partial session whose positions were kept", () => {
    expect(fliesBooks(KEPT)).toEqual([`${KEPT}:control:SPX`]);
    for (const module of ["flies", "meic"] as const) {
      expect(readEntryAttempts(config, module, "paper", KEPT).timeline, module).toHaveLength(1);
    }
  });
});
