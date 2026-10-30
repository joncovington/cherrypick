import { describe, it, expect, beforeAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { meicExitKind, meicHeldSide, readMeicForest } from "../src/readers/meic.js";

/**
 * A condor with one side stopped is `partial` and still open on the other side until settlement.
 * On 2026-10-30 the live condor's put side stopped at 14:53 ET; the forest read only `open` as
 * open, so it said "no position is open", drew the whole condor as entered, released the call
 * strikes too, and the entries table called the row "expired OTM" while the call side was held.
 * The row here is that one.
 */

let config: ConsoleConfig;
const DAY = "2026-10-30";

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-meicpartial-"));
  const dir = path.join(tmp, "meic");
  fs.mkdirSync(dir, { recursive: true });
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(`
    CREATE TABLE ic_trades (
      id INTEGER PRIMARY KEY, ic_order_id TEXT, trade_date TEXT, arm TEXT, symbol TEXT,
      put_strike REAL, call_strike REAL, wing_width REAL, net_credit REAL, quantity INTEGER,
      put_credit REAL, call_credit REAL, put_stop_cost REAL, call_stop_cost REAL,
      status TEXT, underlying_price_entry REAL, pnl REAL, fees REAL, entry_time TEXT, exit_reason TEXT
    );
  `);
  db.prepare(
    `INSERT INTO ic_trades (ic_order_id, trade_date, arm, symbol, put_strike, call_strike, wing_width,
                            net_credit, quantity, put_credit, call_credit, put_stop_cost, status,
                            underlying_price_entry, pnl, fees)
     VALUES ('ic1', ?, 'live', 'SPX', 7530, 7595, 10, 2.2, 1, 1.14, 1.06, 4.65, 'partial', 7561.67, -351, 8.33)`,
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
      manageDir: path.join(tmp, "manage"),
      overviewDir: path.join(tmp, "overview"),
      technicalsDir: path.join(tmp, "technicals"),
      advisorDir: path.join(tmp, "advisor"),
      adviceDir: path.join(tmp, "state", "advice"),
      meicRiskConfig: path.join(tmp, "config.risk.json"),
      fliesConfig: path.join(tmp, "config", "flies.json"),
    },
  };
});

const at = (prices: number[], pnl: number[], spot: number) => pnl[prices.indexOf(spot)];

describe("a condor with one side stopped", () => {
  it("is still an open position, not a resolved book", () => {
    const forest = readMeicForest(config, "paper", DAY);
    expect(forest.openPositions).toBe(1);
    expect(forest.arms).toHaveLength(1);
    expect(forest.arms[0]!.positions[0]!.held).toBe("call");
    expect(forest.arms[0]!.outcome.open).toBe(1);
  });

  it("is drawn as its held side to expiry plus what the stop booked", () => {
    const { prices, pnl } = readMeicForest(config, "paper", DAY).arms[0]!;
    // Below the calls: the call credit kept (+106) on top of the put stop (-351).
    expect(at(prices, pnl, 7580)).toBeCloseTo(-245, 6);
    // Through the put strikes the curve is flat: that side is closed and settles nothing.
    expect(at(prices, pnl, 7520)).toBeCloseTo(-245, 6);
    // Past the long call: the call spread's full loss, (1.06 - 10) x 100, plus the stop.
    expect(at(prices, pnl, 7605)).toBeCloseTo(-894 - 351, 6);
  });

  it("releases only the stopped side's strikes", () => {
    const released = readMeicForest(config, "paper", DAY).releasedStrikes.map((s) => `${s.right}${s.strike}`);
    expect(released.sort()).toEqual(["P7520", "P7530"]);
  });

  it("has no exit kind yet, so it never reads as expired", () => {
    expect(meicExitKind(-351, null, null, null, null, "partial")).toBeNull();
    expect(meicExitKind(-351, null, null, null, null, "expired")).toBe("expired");
  });

  it("names the held side from the stop cost that is unset", () => {
    expect(meicHeldSide({ status: "partial", put_stop_cost: 4.65, call_stop_cost: null })).toBe("call");
    expect(meicHeldSide({ status: "partial", put_stop_cost: null, call_stop_cost: 2.1 })).toBe("put");
    expect(meicHeldSide({ status: "partial", put_stop_cost: null, call_stop_cost: null })).toBeNull();
    expect(meicHeldSide({ status: "stopped", put_stop_cost: 4.65, call_stop_cost: null })).toBeNull();
  });
});
