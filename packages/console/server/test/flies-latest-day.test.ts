import { describe, it, expect, beforeAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readFlies, readFliesAnalytics, CURRENT_ERA } from "../src/readers/flies.js";

/**
 * "latest day" is a DAY, not the absence of one.
 *
 * The Today tab resolves a null date to the most recent session. It used to reach the SQL as no
 * date clause at all for the books and positions tables, so they answered for every session in the
 * era while the cards above them answered for one — the same tab showing 289 rows beside a
 * 34-position day. These pin the resolution, and pin that an EXPLICIT date still wins.
 */

let config: ConsoleConfig;
const TODAY = "2026-08-13";
const YESTERDAY = "2026-08-12";

function seed(dir: string): void {
  fs.mkdirSync(dir, { recursive: true });
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(`
    CREATE TABLE fly_books (
      id INTEGER PRIMARY KEY, book_id TEXT, trade_date TEXT, arm TEXT, symbol TEXT,
      credit_collected REAL, debits_paid REAL, fees REAL, net_cash REAL, floor_holds INTEGER,
      band_low REAL, band_high REAL, pnl REAL, status TEXT
    );
    CREATE TABLE fly_positions (
      id INTEGER PRIMARY KEY, position_id TEXT, trade_date TEXT, symbol TEXT, arm TEXT,
      entry_mode TEXT, kind TEXT, side TEXT, center REAL, wing_width REAL, far_width REAL,
      quantity INTEGER, net REAL, gross_pnl REAL, floor_dollars REAL, risk_free INTEGER,
      status TEXT, pnl REAL, fees REAL, entry_time TEXT, completed_at TEXT
    );
  `);
  const book = db.prepare(
    `INSERT INTO fly_books (book_id, trade_date, arm, symbol, credit_collected, debits_paid, fees,
                            net_cash, floor_holds, band_low, band_high, pnl, status)
     VALUES (?, ?, 'gex', ?, 1, 0, 0.5, 0.5, 1, 0, 0, ?, 'settled')`,
  );
  const pos = db.prepare(
    `INSERT INTO fly_positions (position_id, trade_date, symbol, arm, entry_mode, kind, side, center,
                                wing_width, quantity, net, gross_pnl, floor_dollars, risk_free,
                                status, pnl, fees, entry_time, completed_at)
     VALUES (?, ?, ?, 'gex', 'credit', 'fly', 'put', 5000, 5, 1, 1, ?, 0, 0, 'settled', ?, 0.5, '10:00', '10:30')`,
  );
  // Three sessions in the current era: two positions yesterday, one today.
  for (const [n, date] of [
    [1, YESTERDAY],
    [2, YESTERDAY],
    [3, TODAY],
  ] as Array<[number, string]>) {
    book.run(`b${String(n)}`, date, CURRENT_ERA.symbol, 10);
    pos.run(`p${String(n)}`, date, CURRENT_ERA.symbol, 10, 10);
  }
  db.close();
}

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-flies-day-"));
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
      scoutDir: path.join(tmp, "scout"),
      reviewDir: path.join(tmp, "review"),
      overviewDir: path.join(tmp, "overview"),
      advisorDir: path.join(tmp, "advisor"),
      adviceDir: path.join(tmp, "state", "advice"),
      meicRiskConfig: path.join(tmp, "config.risk.json"),
      fliesConfig: path.join(tmp, "config", "flies.json"),
    },
  };
});

const noDate = { arm: null, date: null, symbol: null, era: null };

describe("a null date means the latest session", () => {
  it("books and positions show that day only, not every day in the era", () => {
    const payload = readFlies(config, "paper", noDate);
    expect(payload.positions.total).toBe(1);
    expect(payload.books.total).toBe(1);
    expect(payload.positions.rows.every((r) => r.tradeDate === TODAY)).toBe(true);
    expect(payload.books.rows.every((r) => r.tradeDate === TODAY)).toBe(true);
  });

  it("the per-arm tables agree with the session card above them", () => {
    const a = readFliesAnalytics(config, "paper", noDate);
    expect(a.today.tradeDate).toBe(TODAY);
    expect(a.today.positions).toBe(1);
    // byArm and the session card must count the same rows — that mismatch was the whole bug.
    expect(a.byArm.reduce((n, r) => n + r.trades, 0)).toBe(a.today.positions);
    expect(a.feeDrag).toHaveLength(1);
  });
});

describe("an explicit date still wins", () => {
  it("pins every table to the day asked for", () => {
    const filter = { ...noDate, date: YESTERDAY };
    const payload = readFlies(config, "paper", filter);
    expect(payload.positions.total).toBe(2);
    expect(payload.positions.rows.every((r) => r.tradeDate === YESTERDAY)).toBe(true);

    const a = readFliesAnalytics(config, "paper", filter);
    expect(a.today.tradeDate).toBe(YESTERDAY);
    expect(a.byArm.reduce((n, r) => n + r.trades, 0)).toBe(2);
  });
});

describe("a book with no sessions at all", () => {
  it("reads empty rather than throwing", () => {
    const empty: ConsoleConfig = { ...config, paths: { ...config.paths, fliesDir: path.join(config.paths.cherrypick, "nope") } };
    expect(readFlies(empty, "paper", noDate).positions.total).toBe(0);
    expect(readFliesAnalytics(empty, "paper", noDate).today.tradeDate).toBeNull();
  });
});

describe("a session the loop worked through but took nothing", () => {
  /**
   * The reason the day resolver reads more than one table.
   *
   * fly_iterations is written on every tick; fly_positions only when something is entered. So a
   * barren session -- which this module has by design -- leaves the two tables disagreeing about
   * what "latest" means, and a card resolving from positions alone would quietly show the previous
   * session next to a timeline showing today.
   */
  const BARREN = "2026-08-14";
  let barrenConfig: ConsoleConfig;

  beforeAll(() => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "flies-barren-"));
    const fliesDir = path.join(dir, "flies");
    seed(fliesDir);
    const db = new Database(path.join(fliesDir, "paper_trades.db"));
    db.exec(`CREATE TABLE fly_iterations (
      id INTEGER PRIMARY KEY, trade_date TEXT, iteration_ts TEXT, arm TEXT,
      center REAL, underlying_price REAL
    );`);
    // The loop ran on a later day than any position exists for.
    db.prepare(
      "INSERT INTO fly_iterations (trade_date, iteration_ts, arm, center, underlying_price) VALUES (?,?,?,?,?)",
    ).run(BARREN, `${BARREN}T10:00:00-04:00`, "gex", 5000, 5001);
    db.close();
    barrenConfig = { paths: { fliesDir } } as unknown as ConsoleConfig;
  });

  it("resolves the latest session from the tick journal, not just from entries", () => {
    const a = readFliesAnalytics(barrenConfig, "paper", { arm: null, date: null, symbol: null, era: null });
    expect(a.today.tradeDate).toBe(BARREN);
    expect(a.today.positions).toBe(0); // barren is a real answer, not a missing one
  });
});
