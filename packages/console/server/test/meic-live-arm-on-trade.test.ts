import { describe, it, expect } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { registryArm } from "../src/readers/meicLive.js";

/**
 * A live trade's strategy arm: its own `live_arm` column (written at entry) first, its filled entry
 * attempt second, none where neither exists. The ledger's `arm` column is the account's constant
 * `live` and is never the answer.
 */

function ledger(withColumn: boolean, withAttempts: boolean): Database.Database {
  const db = new Database(path.join(fs.mkdtempSync(path.join(os.tmpdir(), "meic-arm-")), "meic_trades.db"));
  db.exec(
    `CREATE TABLE ic_trades (id INTEGER PRIMARY KEY, ic_order_id TEXT, arm TEXT${withColumn ? ", live_arm TEXT" : ""})`,
  );
  if (withAttempts) {
    db.exec("CREATE TABLE entry_attempts (id INTEGER PRIMARY KEY, ic_order_id TEXT, arm TEXT, outcome TEXT)");
  }
  return db;
}

function armsById(db: Database.Database): Record<number, string | null> {
  const rows = db.prepare<[], { id: number; a: string | null }>(`SELECT id, ${registryArm(db)} AS a FROM ic_trades ORDER BY id`).all();
  return Object.fromEntries(rows.map((r) => [r.id, r.a]));
}

describe("registryArm", () => {
  it("prefers the column over the attempts join", () => {
    const db = ledger(true, true);
    db.exec(`INSERT INTO ic_trades VALUES (1, 'A', 'live', 'pm-200');
             INSERT INTO entry_attempts VALUES (1, 'A', 'other-arm', 'filled')`);
    expect(armsById(db)).toEqual({ 1: "pm-200" });
  });

  it("falls back to the filled attempt where the column is NULL, and to nothing where there is none", () => {
    const db = ledger(true, true);
    db.exec(`INSERT INTO ic_trades VALUES (1, 'A', 'live', NULL), (2, 'B', 'live', NULL), (3, 'C', 'live', NULL);
             INSERT INTO entry_attempts VALUES (1, 'A', 'pm-200', 'filled'), (2, 'C', 'pm-200', 'no_fill')`);
    expect(armsById(db)).toEqual({ 1: "pm-200", 2: null, 3: null });
  });

  it("reads an old ledger with no column through the join", () => {
    const db = ledger(false, true);
    db.exec(`INSERT INTO ic_trades VALUES (1, 'A', 'live'); INSERT INTO entry_attempts VALUES (1, 'A', 'pm-200', 'filled')`);
    expect(armsById(db)).toEqual({ 1: "pm-200" });
  });

  it("reads the column on a ledger with no attempts table", () => {
    const db = ledger(true, false);
    db.exec(`INSERT INTO ic_trades VALUES (1, 'A', 'live', 'pm-200'), (2, 'B', 'live', NULL)`);
    expect(armsById(db)).toEqual({ 1: "pm-200", 2: null });
  });

  it("is NULL on a ledger with neither", () => {
    const db = ledger(false, false);
    db.exec(`INSERT INTO ic_trades VALUES (1, 'A', 'live')`);
    expect(armsById(db)).toEqual({ 1: null });
  });
});
