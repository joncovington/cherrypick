import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readFliesAnalytics } from "../src/readers/flies.js";
import { closePooledDbs } from "../src/readers/db.js";

/**
 * The session tab's "worst case at expiry" summed only OPEN positions, so on 2026-09-24 it read $0
 * the moment the live pilot settled while the page still showed that session -- a day whose book
 * had carried $283.44 of worst case. The live figure is now the session's peak from the loop's own
 * `fly_live_marks.open_margin`, held until the paper loop records the next session's 09:30 open.
 * `completed` is pinned beside it: the completion tile named only the open count ("0 still open of
 * 4 entered") beside 75%.
 */

const DDL = `
CREATE TABLE fly_positions (position_id TEXT, trade_date TEXT, arm TEXT, kind TEXT, side TEXT, center REAL,
  wing_width REAL, far_width REAL, net REAL, quantity INTEGER, fees REAL, risk_free INTEGER, completed_at TEXT,
  gross_pnl REAL, pnl REAL, status TEXT);
CREATE TABLE fly_live_marks (id INTEGER PRIMARY KEY, iteration_ts TEXT, trade_date TEXT, position_id TEXT,
  mark_pnl REAL, open_margin REAL);
`;

let tmp: string;
let config: ConsoleConfig;
const noFilter = { arm: null, date: null, symbol: null, era: null };

function live(): Database.Database {
  return new Database(path.join(tmp, "live_trades.db"));
}

function paperSnapshots(rows: Array<[string, string]>): void {
  const db = new Database(path.join(tmp, "paper_trades.db"));
  db.exec("CREATE TABLE fly_snapshots (id INTEGER PRIMARY KEY, iteration_ts TEXT, trade_date TEXT, status TEXT)");
  const ins = db.prepare("INSERT INTO fly_snapshots (iteration_ts, trade_date, status) VALUES (?, ?, 'ok')");
  for (const [ts, day] of rows) ins.run(ts, day);
  db.close();
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-flies-peak-"));
  config = { port: 0, paths: { fliesDir: tmp } } as unknown as ConsoleConfig;
  const db = live();
  db.exec(DDL);
  const pos = db.prepare(
    `INSERT INTO fly_positions (position_id, trade_date, arm, kind, side, center, wing_width, net, quantity, fees,
       risk_free, completed_at, pnl, status) VALUES (?, '2026-09-24', 'control', ?, 'put', 7690, 5, ?, 1, 6.89, ?, ?, ?, 'settled')`,
  );
  pos.run("a", "fly", 0.25, 1, "2026-09-24T12:16:57-04:00", 18.11);
  pos.run("b", "fly", 0.25, 1, "2026-09-24T12:18:24-04:00", 421.11);
  pos.run("c", "fly", 0.25, 1, "2026-09-24T13:43:42-04:00", 95.11);
  pos.run("d", "short_vertical", 2.55, 0, null, -258.44);
  const mark = db.prepare(
    "INSERT INTO fly_live_marks (iteration_ts, trade_date, position_id, mark_pnl, open_margin) VALUES (?, '2026-09-24', ?, 0, ?)",
  );
  mark.run("2026-09-24T10:31:25-04:00", "a", 283.44);
  mark.run("2026-09-24T12:20:00-04:00", "a", 3.11);
  mark.run("2026-09-24T14:05:06-04:00", "d", 258.44);
  db.close();
});

afterEach(() => {
  closePooledDbs();
  fs.rmSync(tmp, { recursive: true, force: true });
});

describe("the live session's worst case at expiry after settlement", () => {
  it("holds the session peak through the evening, and counts completions for the tile", () => {
    paperSnapshots([["2026-09-24T15:59:45-04:00", "2026-09-24"]]);
    const t = readFliesAnalytics(config, "live", noFilter).today;
    expect(t.open).toBe(0);
    expect(t.maxPossibleLoss).toBe(0);
    expect(t.sessionPeakWorst).toEqual({ worst: -283.44, at: "2026-09-24T10:31:25-04:00" });
    expect([t.completed, t.positions, t.completionPct]).toEqual([3, 4, 75]);
  });

  it("still holds it before the next session's 09:30 open", () => {
    paperSnapshots([["2026-09-25T09:29:00-04:00", "2026-09-25"]]);
    expect(readFliesAnalytics(config, "live", noFilter).today.sessionPeakWorst?.worst).toBe(-283.44);
  });

  it("lets go once the paper loop records the next session's open", () => {
    paperSnapshots([["2026-09-25T09:30:00-04:00", "2026-09-25"]]);
    const t = readFliesAnalytics(config, "live", noFilter).today;
    expect(t.positions).toBe(4); // a real read, not withReadOnlyDb's empty fallback
    expect(t.sessionPeakWorst).toBeNull();
  });

  it("is a live-only figure", () => {
    fs.renameSync(path.join(tmp, "live_trades.db"), path.join(tmp, "paper_trades.db"));
    const t = readFliesAnalytics(config, "paper", noFilter).today;
    expect(t.positions).toBe(4);
    expect(t.sessionPeakWorst).toBeNull();
  });
});
