import { describe, it, expect, beforeAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readMeicForest } from "../src/readers/meic.js";

/**
 * An `equalize_credit` pair sizes its two wings independently (`put_wing`/`call_wing`), with
 * `wing_width` holding the WIDER of the two. Three readers in the forest drew the long legs from
 * the single scalar, which placed the NARROWER side's long at the wider width — so the payoff curve
 * and the released-strikes list were both wrong for the narrower side. These pin each side to its
 * own wing through the real reader.
 *
 * The row below is a 35-wide put / 25-wide call pair, shorts 7450/7520 (longs 7415/7545).
 */

let config: ConsoleConfig;
const DAY = "2026-08-13";

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-meicforest-eqpair-"));
  const dir = path.join(tmp, "meic");
  fs.mkdirSync(dir, { recursive: true });
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(`
    CREATE TABLE ic_trades (
      id INTEGER PRIMARY KEY, ic_order_id TEXT, trade_date TEXT, risk_profile TEXT, symbol TEXT,
      put_strike REAL, call_strike REAL, wing_width REAL, put_wing REAL, call_wing REAL,
      net_credit REAL, quantity INTEGER, status TEXT, underlying_price_entry REAL, pnl REAL,
      fees REAL, entry_time TEXT, exit_reason TEXT
    );
  `);
  // The pair: put wing 35 (long 7415), call wing 25 (long 7545); wing_width is the wider (35).
  // Stopped so it reaches the released-strikes list too.
  db.prepare(
    `INSERT INTO ic_trades (ic_order_id, trade_date, risk_profile, symbol, put_strike, call_strike,
                            wing_width, put_wing, call_wing, net_credit, quantity, status,
                            underlying_price_entry, pnl, fees)
     VALUES ('pair', ?, 'be-hourly-1x', 'SPX', 7450, 7520, 35, 35, 25, 2.0, 1, 'stopped', 7485, -50, 5)`,
  ).run(DAY);
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
      calendarsDir: path.join(tmp, "calendars"),
      bwbDir: path.join(tmp, "bwb"),
      contangoDir: path.join(tmp, "contango"),
      curveDir: path.join(tmp, "curve"),
    },
  } as unknown as ConsoleConfig;
});

describe("the forest places each long at its own side's wing on an equalized pair", () => {
  it("caps the narrower call side at 25, not the wider 35", () => {
    const arm = readMeicForest(config, "paper", DAY).asEntered.find((a) => a.arm === "be-hourly-1x")!;
    expect(arm).toBeDefined();

    const pos = arm.positions[0];
    // The per-side widths travel out, so a reader can see the asymmetry rather than infer it.
    expect(pos.putWing).toBe(35);
    expect(pos.callWing).toBe(25);

    // At the far right of the grid the short call is deep ITM, so the curve is flat at the wing's
    // full width: the loss stops at 25 (the call long at 7545), giving credit 2.0 - 25 = -23. Drawn
    // at the scalar 35 it would read -33. The last grid point is past both longs, so this is the
    // cleanest place to see which width the reader used.
    const last = arm.pnl[arm.pnl.length - 1]!;
    expect(last).toBeCloseTo(-2300, 6);
  });

  it("releases the narrower side's long strike at its own wing", () => {
    const forest = readMeicForest(config, "paper", DAY);
    const pair = forest.releasedStrikes.filter((s) => s.arm === "be-hourly-1x");
    // Shorts: 7450P and 7520C. Longs: 7450-35=7415P and 7520+25=7545C.
    const strikes = pair.map((s) => `${s.right}${s.strike}`).sort();
    expect(strikes).toEqual(["C7520", "C7545", "P7415", "P7450"].sort());
    // The scalar 35 would have released a call long at 7555 instead.
    expect(pair.some((s) => s.right === "C" && s.strike === 7555)).toBe(false);
  });
});
