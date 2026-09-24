import { describe, it, expect, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { loadConfig } from "../src/config.js";
import { readCurve } from "../src/readers/curve.js";
import { closePooledDbs } from "../src/readers/db.js";

/**
 * Curve's open-position P&L. `close_cost` is what buying the spread back costs (`short_mid -
 * long_mid`, positive for a credit spread), so the mark is `entry_credit - close_cost` -- the
 * module's own `analytics.excursions`. Until 2026-09-24 the reader ADDED the two, so a spread marked
 * at exactly its credit read +$200 on 2 contracts instead of $0. And after an ITM expiry, while the
 * delivered shares wait for disposal, there is no leg left to mark: the position is priced from its
 * settled legs and its held shares, never from the last pre-expiry close_cost.
 */

let dir: string;

function ledger(): Database.Database {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "curve-unreal-"));
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(`
    CREATE TABLE curve_positions (id INTEGER PRIMARY KEY, position_id TEXT, symbol TEXT, arm TEXT,
      entry_session TEXT, status TEXT, exit_reason TEXT, gross_pnl REAL, fees REAL, quantity INTEGER,
      entry_credit REAL, short_strike REAL, long_strike REAL, expiration TEXT);
    CREATE TABLE curve_legs (id INTEGER PRIMARY KEY, position_id TEXT, leg_role TEXT, action TEXT,
      entry_mid REAL, status TEXT, close_value REAL);
    CREATE TABLE curve_marks (id INTEGER PRIMARY KEY, position_id TEXT, leg_role TEXT, session_date TEXT,
      mid REAL, close_cost REAL, short_tv REAL, spot REAL, assignment_exposed INTEGER, usable INTEGER,
      refusal TEXT, marked_at REAL);
    CREATE TABLE curve_assignments (id INTEGER PRIMARY KEY, position_id TEXT, leg_role TEXT, direction TEXT,
      shares INTEGER, basis REAL, status TEXT);
    CREATE TABLE curve_regime (id INTEGER PRIMARY KEY, trade_date TEXT UNIQUE, ratio REAL, regime TEXT,
      hook INTEGER, vix REAL, vix3m REAL, usable INTEGER, refusal TEXT);
    CREATE TABLE curve_loop_iterations (id INTEGER PRIMARY KEY, ran_at REAL, session_date TEXT,
      phase TEXT, status TEXT);
    CREATE TABLE measurement_breaks (id INTEGER PRIMARY KEY, break_date TEXT, key TEXT, note TEXT);
  `);
  db.prepare(
    `INSERT INTO curve_positions (position_id, symbol, arm, entry_session, status, fees, quantity, entry_credit,
       short_strike, long_strike, expiration) VALUES ('P', 'VXX', 'control', '2026-09-21', 'open', 6.49, 2, 1.0, 50, 55, '2026-10-16')`,
  ).run();
  return db;
}

function openPosition() {
  const config = loadConfig();
  config.paths.curveDir = dir;
  const rows = readCurve(config).openPositions;
  expect(rows).toHaveLength(1);
  return rows[0]!;
}

afterEach(() => {
  closePooledDbs();
  fs.rmSync(dir, { recursive: true, force: true });
});

describe("curve's open-position P&L", () => {
  it("reads a spread marked at its own credit as flat, and a cheaper buy-back as profit", () => {
    const db = ledger();
    db.exec(`INSERT INTO curve_legs (position_id, leg_role, action, entry_mid, status) VALUES
      ('P', 'short_call', 'Sell to Open', 2.0, 'open'), ('P', 'long_call', 'Buy to Open', 1.0, 'open')`);
    db.prepare(
      "INSERT INTO curve_marks (position_id, close_cost, spot, usable, marked_at) VALUES ('P', 1.0, 45.0, 1, 1)",
    ).run();
    db.close();
    expect(openPosition().unrealisedGross).toBe(0);

    const again = new Database(path.join(dir, "paper_trades.db"));
    again.prepare(
      "INSERT INTO curve_marks (position_id, close_cost, spot, usable, marked_at) VALUES ('P', 0.4, 44.0, 1, 2)",
    ).run();
    again.close();
    closePooledDbs();
    const p = openPosition();
    expect(p.unrealisedGross).toBe(120); // (1.00 - 0.40) x 100 x 2
    expect(p.unrealisedNet).toBe(113.51);
  });

  it("prices an expired spread awaiting share disposal from its settled legs, not its last mark", () => {
    const db = ledger();
    // Settled through both strikes at 60: short 50 call worth 10, long 55 call worth 5.
    db.exec(`INSERT INTO curve_legs (position_id, leg_role, action, entry_mid, status, close_value) VALUES
      ('P', 'short_call', 'Sell to Open', 2.0, 'settled', 10.0), ('P', 'long_call', 'Buy to Open', 1.0, 'settled', 5.0)`);
    // The last pre-expiry mark: a close_cost the old reader kept pricing from.
    db.prepare(
      "INSERT INTO curve_marks (position_id, close_cost, spot, usable, marked_at) VALUES ('P', 4.5, 58.0, 1, 1)",
    ).run();
    // Both legs assigned at the 60 settlement: short 200 shares, long 200 shares -- they cancel.
    db.exec(`INSERT INTO curve_assignments (position_id, leg_role, direction, shares, basis, status) VALUES
      ('P', 'short_call', 'short', 200, 60.0, 'open'), ('P', 'long_call', 'long', 200, 60.0, 'open')`);
    db.close();
    const p = openPosition();
    // legs: (2.00 - 10.00) + (5.00 - 1.00) = -4.00 per share x 100 x 2 = -800; shares net 0.
    expect(p.unrealisedGross).toBe(-800);
    expect(p.currentCloseCost).toBeNull();
  });
});
