import { beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readFliesAnalytics, readFliesCosts } from "../src/readers/flies.js";

/**
 * A voided flies row keeps `status = 'settled'` and its pnl; only `void_reason` says it was struck
 * (the bwb roll defect of 2026-08-04..06, the 2026-10-08 outage). The per-arm table, the fee drag
 * table, the session card and the costs page each filtered on `pnl IS NOT NULL` or
 * `status = 'settled'` alone, so the struck rows were summed into them: 47 rows and -$994.41 on the
 * paper ledger. One held and one voided position on the same day and arm; only the held one counts.
 */

const DAY = "2026-10-08";
let config: ConsoleConfig;

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-flies-void-"));
  const dir = path.join(tmp, "flies");
  fs.mkdirSync(dir, { recursive: true });
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(`
    CREATE TABLE fly_positions (
      id INTEGER PRIMARY KEY, position_id TEXT, trade_date TEXT, symbol TEXT, arm TEXT,
      entry_mode TEXT, kind TEXT, side TEXT, center REAL, wing_width REAL, far_width REAL,
      quantity INTEGER, net REAL, gross_pnl REAL, floor_dollars REAL, risk_free INTEGER,
      status TEXT, pnl REAL, fees REAL, entry_time TEXT, completed_at TEXT, void_reason TEXT
    );
  `);
  const pos = db.prepare(
    `INSERT INTO fly_positions (position_id, trade_date, symbol, arm, entry_mode, kind, side, center,
                                wing_width, quantity, net, gross_pnl, floor_dollars, risk_free,
                                status, pnl, fees, entry_time, completed_at, void_reason)
     VALUES (?, ?, 'SPX', 'control', 'legged', 'fly', 'put', 6700, 5, 1, ?, ?, 0, 0, 'settled', ?, ?, '10:00', '10:30', ?)`,
  );
  pos.run("held", DAY, 1.0, 50, 43.11, 6.89, null);
  pos.run("struck", DAY, 2.0, -500, -506.89, 6.89, "power outage");
  db.close();
  config = { paths: { fliesDir: dir } } as unknown as ConsoleConfig;
});

describe("voided flies rows are in no money total", () => {
  it("the per-arm and fee drag tables and the session card count the held row only", () => {
    const a = readFliesAnalytics(config, "paper", { arm: null, date: DAY, symbol: null, era: "ALL" });
    expect(a.byArm).toEqual([expect.objectContaining({ arm: "control", trades: 1, net: 43.11 })]);
    expect(a.feeDrag).toEqual([expect.objectContaining({ arm: "control", gross: 50, fees: 6.89, net: 43.11 })]);
    expect(a.today.positions).toBe(1);
    expect(a.today.netPnl).toBe(43.11);
  });

  it("the costs page counts the held row only", () => {
    const c = readFliesCosts(config, "paper", "ALL");
    expect(c.arms).toEqual([expect.objectContaining({ arm: "control", positions: 1, grossPnl: 50, premium: 100 })]);
  });
});
