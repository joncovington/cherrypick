import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readMeicLiveGap } from "../src/readers/meicLiveGap.js";

/**
 * MEIC live against paper (docs/console-review-plan.md batch 5). The guards: a live condor pairs with
 * the paper twin on the same strikes before entry order; paper sessions live sat out are counted on
 * their own line; an open condor has no net and no gap; the paired totals are the sums of their
 * rows; other paper arms and paper sessions before live began are not read; a missing ledger is
 * "absent", not a thrown route.
 */

let tmp: string;
let config: ConsoleConfig;

const SCHEMA = `CREATE TABLE ic_trades (
  id INTEGER PRIMARY KEY, trade_date TEXT, entry_time TEXT, symbol TEXT, put_strike REAL, call_strike REAL,
  wing_width REAL, net_credit REAL, quantity INTEGER, status TEXT, pnl REAL, fees REAL, arm TEXT)`;

type Row = [string, string, number, number, number, string, number | null, number | null, string];

function ledger(file: string, rows: Row[]): void {
  const db = new Database(path.join(tmp, file));
  db.exec(SCHEMA);
  const ins = db.prepare(
    `INSERT INTO ic_trades (trade_date, entry_time, symbol, put_strike, call_strike, wing_width, net_credit, quantity,
       status, pnl, fees, arm) VALUES (?, ?, 'SPX', ?, ?, 10, ?, 1, ?, ?, ?, ?)`,
  );
  for (const r of rows) ins.run(...r);
  db.close();
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "meic-live-gap-"));
  config = { paths: { meicDir: tmp } } as unknown as ConsoleConfig;
  ledger("meic_trades.db", [
    // 10-20: two live condors; the second entered first's strikes on paper first
    ["2026-10-20", "2026-10-20 14:00", 7600, 7650, 1.6, "expired", 160, 7, "live"],
    ["2026-10-20", "2026-10-20 14:10", 7610, 7660, 1.5, "stopped", -200, 8, "live"],
    // 10-22: open
    ["2026-10-22", "2026-10-22 14:00", 7700, 7750, 2.0, "open", null, 6, "live"],
    ["2026-10-22", "2026-10-22 14:05", 7700, 7750, 2.0, "cancelled", null, null, "live"],
  ]);
  ledger("paper_trades.db", [
    ["2026-10-19", "2026-10-19 14:00", 7500, 7550, 1.7, "expired", 170, 5, "live-shadow"], // before live began
    ["2026-10-20", "2026-10-20 13:58", 7610, 7660, 1.55, "stopped", -190, 5, "live-shadow"],
    ["2026-10-20", "2026-10-20 14:01", 7600, 7650, 1.62, "expired", 162, 5, "live-shadow"],
    ["2026-10-20", "2026-10-20 14:00", 7600, 7650, 1.62, "expired", 999, 5, "control"],
    ["2026-10-21", "2026-10-21 14:00", 7650, 7700, 1.8, "expired", 180, 5, "live-shadow"], // live sat out
    ["2026-10-22", "2026-10-22 14:00", 7700, 7750, 2.01, "open", null, 5, "live-shadow"],
  ]);
});

describe("readMeicLiveGap", () => {
  it("pairs the same strikes first, counts what live sat out, and leaves open condors without a gap", () => {
    const g = readMeicLiveGap(config);
    expect(g.ledger).toBe("ok");
    expect(g.since).toBe("2026-10-20");
    expect(g.rows.map((r) => r.session)).toEqual(["2026-10-22", "2026-10-21", "2026-10-20", "2026-10-20"]);
    const [open, satOut, second, first] = g.rows;
    expect(open!.live?.status).toBe("open");
    expect(open!.gap).toBeNull();
    expect(satOut!.live).toBeNull();
    expect(satOut!.paper?.net).toBe(175);
    // 7600/7650: live 160 - 7 against paper 162 - 5
    expect(first!.live?.putStrike).toBe(7600);
    expect(first!.paper?.putStrike).toBe(7600);
    expect(first!.gap).toBe(-4);
    // 7610/7660: live -200 - 8 against paper -190 - 5
    expect(second!.paper?.putStrike).toBe(7610);
    expect(second!.gap).toBe(-13);
  });

  it("totals the paired rows to the cent and keeps the sat-out sessions apart", () => {
    const t = readMeicLiveGap(config).totals;
    expect(t).toEqual({
      paired: 2,
      liveNet: -55, // 153 + (-208)
      paperNet: -38, // 157 + (-195)
      gap: -17,
      paperOnly: 1,
      paperOnlyNet: 175,
      liveOnly: 0,
    });
  });

  it("is absent, not a thrown route, without a live ledger", () => {
    fs.rmSync(path.join(tmp, "meic_trades.db"));
    expect(readMeicLiveGap(config)).toMatchObject({ ledger: "absent", rows: [], since: null });
  });
});
