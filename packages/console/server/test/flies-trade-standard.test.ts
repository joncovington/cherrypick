import { describe, it, expect, beforeAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import {
  readFlies,
  readFliesAnalytics,
  readFliesHistory,
  readFliesTradeLog,
  NO_TRADE_LOG_QUERY,
  CURRENT_ERA,
} from "../src/readers/flies.js";

/**
 * The trade table standard (packages/console/CLAUDE.md) on the flies tables.
 *
 * Every money column is whole-position dollars with a signed cash flow, and a row adds up left to
 * right: entry + exit = gross, gross − fees − settlement = net. `fees` in the ledger is the TOTAL
 * and `settlement_fees` a component of it, so the reader must take one out of the other exactly
 * once — shown twice, a row would overstate its costs by the settlement fee; shown not at all, the
 * settlement column would be a number nothing adds up to.
 */

let config: ConsoleConfig;
const SYM = CURRENT_ERA.symbol;
const DAY = "2026-09-24";

function seed(dir: string): void {
  fs.mkdirSync(dir, { recursive: true });
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(`
    CREATE TABLE fly_books (
      id INTEGER PRIMARY KEY, book_id TEXT, trade_date TEXT, arm TEXT, symbol TEXT,
      credit_collected REAL, debits_paid REAL, fees REAL, net_cash REAL, floor_holds INTEGER,
      band_low REAL, band_high REAL, pnl REAL, status TEXT, settlement_price REAL,
      settlement_source TEXT, updated_at TEXT
    );
    CREATE TABLE fly_positions (
      id INTEGER PRIMARY KEY, position_id TEXT, book_id TEXT, trade_date TEXT, entry_time TEXT,
      symbol TEXT, arm TEXT, entry_mode TEXT, kind TEXT, side TEXT, center REAL, wing_width REAL,
      far_width REAL, entry_window TEXT, quantity INTEGER, net REAL, gross_pnl REAL, fees REAL,
      settlement_fees REAL, slippage_dollars REAL, expiry_payoff REAL, closed_before_expiry INTEGER,
      floor_dollars REAL, risk_free INTEGER, pnl REAL, completion_latency_min REAL, pinned INTEGER,
      status TEXT, void_reason TEXT, completed_at TEXT
    );
  `);
  const pos = db.prepare(
    `INSERT INTO fly_positions (position_id, book_id, trade_date, entry_time, symbol, arm, entry_mode,
       kind, side, center, wing_width, quantity, net, gross_pnl, fees, settlement_fees,
       slippage_dollars, expiry_payoff, closed_before_expiry, floor_dollars, risk_free, pnl, pinned,
       status, void_reason)
     VALUES (?, 'b1', ?, ? || 'T10:00:00-04:00', ?, 'control', 'legged', 'fly', 'put', 6000, 5,
             ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?, 0, ?, ?)`,
  );
  // Settled in the money: 1.20 cr x2 = +240 entry, payoff -0.50 x2 = -100 exit, gross 140;
  // fees 40 of which 10 is settlement, so trading 30 and net 100.
  pos.run("p1", DAY, DAY, SYM, 2, 1.2, 140, 40, 10, 6, -0.5, 0, 100, "settled", null);
  // Expired worthless: a 0.40 debit, nothing back.
  pos.run("p2", DAY, DAY, SYM, 1, -0.4, -40, 5, 0, null, 0, 0, -45, "settled", null);
  // Closed early at a quote (the retired pre-close exit).
  pos.run("p3", DAY, DAY, SYM, 1, 1.0, 60, 5, null, null, 0.4, 1, 55, "settled", null);
  // Never a position: a cancelled entry and a voided row.
  pos.run("p4", DAY, DAY, SYM, 1, 2.0, null, 0, null, null, null, 0, null, "cancelled", null);
  pos.run("p5", DAY, DAY, SYM, 1, 2.0, 200, 5, 0, null, 0, 0, 195, "settled", "duplicate");
  // The book records the three held positions: credit 240+100, debit 40, fees 50, pnl 110.
  db.prepare(
    `INSERT INTO fly_books (book_id, trade_date, arm, symbol, credit_collected, debits_paid, fees,
                            net_cash, floor_holds, band_low, band_high, pnl, status,
                            settlement_price, settlement_source, updated_at)
     VALUES ('b1', ?, 'control', ?, 340, 40, 50, 250, 1, 5990, 6010, 110, 'settled',
             6004.5, 'last_trade', ? || 'T16:05:00-04:00')`,
  ).run(DAY, SYM, DAY);
  // Completion latencies on the three held positions, 4 / 10 / 30 minutes: median 10. The cancelled
  // entry's 90 must not count.
  const lat = db.prepare("UPDATE fly_positions SET completion_latency_min = ? WHERE position_id = ?");
  for (const [m, id] of [[4, "p1"], [10, "p2"], [30, "p3"], [90, "p4"]] as Array<[number, string]>) lat.run(m, id);
  db.close();
}

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-flies-standard-"));
  seed(path.join(tmp, "flies"));
  config = {
    port: 0,
    paths: {
      cherrypick: tmp,
      streamCacheDb: path.join(tmp, "stream_cache.db"),
      watchdogLast: path.join(tmp, "watchdog.last.json"),
      orchestratorConfig: path.join(tmp, "config.json"),
      consoleData: path.join(tmp, "console"),
      meicDir: path.join(tmp, "meic"),
      fliesDir: path.join(tmp, "flies"),
      earningsDir: path.join(tmp, "earnings"),
      gexDir: path.join(tmp, "gex"),
      reviewDir: path.join(tmp, "review"),
      overviewDir: path.join(tmp, "overview"),
      technicalsDir: path.join(tmp, "technicals"),
      advisorDir: path.join(tmp, "advisor"),
      adviceDir: path.join(tmp, "state", "advice"),
      meicRiskConfig: path.join(tmp, "config.risk.json"),
      fliesConfig: path.join(tmp, "config", "flies.json"),
    },
  };
});

const log = () => readFliesTradeLog(config, "paper", { ...NO_TRADE_LOG_QUERY, era: "ALL" });
const byPrice = (price: number) => log().rows.find((r) => r.price === price);

describe("a trade log row", () => {
  it("signs the entry and exit as cash flows in whole-position dollars", () => {
    const r = byPrice(1.2);
    expect(r?.quantity).toBe(2);
    expect(r?.entryCash).toBe(240);
    expect(r?.exitCash).toBe(-100);
    expect(byPrice(-0.4)?.entryCash).toBe(-40);
  });

  it("adds up: entry + exit = gross, gross - fees - settlement = net", () => {
    for (const r of log().rows) {
      expect((r.entryCash ?? 0) + (r.exitCash ?? 0)).toBeCloseTo(r.gross ?? NaN, 2);
      expect((r.gross ?? 0) - (r.fees ?? 0) - (r.settlementFees ?? 0)).toBeCloseTo(r.pnl ?? NaN, 2);
    }
  });

  it("takes settlement out of the fee total exactly once", () => {
    const r = byPrice(1.2);
    expect(r?.fees).toBe(30);
    expect(r?.settlementFees).toBe(10);
  });

  it("keeps the fee total when the settlement split was never recorded", () => {
    const r = byPrice(1.0);
    expect(r?.settlementFees).toBeNull();
    expect(r?.fees).toBe(5);
  });

  it("names how the position left", () => {
    expect(byPrice(1.2)?.exitKind).toBe("settled");
    expect(byPrice(-0.4)?.exitKind).toBe("expired");
    expect(byPrice(1.0)?.exitKind).toBe("closed");
  });

  it("carries slippage as a measure beside gross, never inside the arithmetic", () => {
    expect(byPrice(1.2)?.slippage).toBe(6);
    expect(byPrice(1.2)?.gross).toBe(140);
    const t = log().totals;
    expect(t.slippage).toBe(6);
    expect(t.slippageTrades).toBe(1);
  });

  it("totals split the fees the same way the rows do", () => {
    const t = log().totals;
    expect(t.grossPnl).toBe(160);
    expect(t.fees).toBe(40);
    expect(t.settlementFees).toBe(10);
    expect(t.netPnl).toBe(110);
  });
});

describe("the books and positions tables", () => {
  const payload = () => readFlies(config, "paper", { arm: null, date: DAY, symbol: null, era: "ALL" });

  it("lists only what was held — no cancelled entry, no voided row", () => {
    const ids = payload().positions.rows.map((p) => p.positionId).sort();
    expect(ids).toEqual(["p1", "p2", "p3"]);
  });

  it("gives each position its entry in signed whole-position dollars", () => {
    const p1 = payload().positions.rows.find((p) => p.positionId === "p1");
    expect(p1?.entryCash).toBe(240);
  });

  it("adds a book up the same way, with its own recorded pnl as the net", () => {
    const b = payload().books.rows[0];
    expect(b?.entryCash).toBe(300);
    expect(b?.gross).toBe(160);
    expect(b?.exitCash).toBe(-140);
    expect(b?.settlementFees).toBe(10);
    expect(b?.fees).toBe(40);
    expect((b?.gross ?? 0) - (b?.fees ?? 0) - (b?.settlementFees ?? 0)).toBeCloseTo(b?.pnl ?? NaN, 2);
  });

  it("leaves a book's slippage unknown unless every held position recorded it", () => {
    expect(payload().books.rows[0]?.slippage).toBeNull();
  });
});

describe("the session tiles' settlement and timing", () => {
  const today = () => readFliesAnalytics(config, "paper", { arm: null, date: DAY, symbol: null, era: "ALL" }).today;

  it("names the print the session settled against and where it came from", () => {
    expect(today().settlement).toEqual({ price: 6004.5, source: "last_trade", at: `${DAY}T16:05:00-04:00` });
  });

  it("takes the median completion time over held positions only", () => {
    expect(today().medianCompletionMin).toBe(10);
  });
});

describe("flies' History summaries follow the date range", () => {
  it("counts the fixture session inside the range and nothing outside it", () => {
    const all = readFliesHistory(config, "paper", { arm: null, date: null, symbol: null, era: "ALL" });
    const inside = readFliesHistory(config, "paper", { arm: null, date: null, symbol: null, era: "ALL" }, { from: DAY, to: DAY });
    const outside = readFliesHistory(config, "paper", { arm: null, date: null, symbol: null, era: "ALL" }, { from: null, to: "2026-09-23" });
    expect(all.dailyPnl.map((d) => d.date)).toEqual([DAY]);
    expect(inside.dailyPnl).toEqual(all.dailyPnl);
    expect(inside.byArm).toEqual(all.byArm);
    expect(outside.dailyPnl).toEqual([]);
    expect(outside.byArm).toEqual([]);
  });
});
