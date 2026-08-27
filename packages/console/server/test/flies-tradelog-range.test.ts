import { describe, it, expect, beforeAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readFliesTradeLog, NO_TRADE_LOG_QUERY, CURRENT_ERA } from "../src/readers/flies.js";

/**
 * Explicit date bounds on the trade log.
 *
 * The search box could already match a date as TEXT, which answers "2026-08" but cannot answer
 * "the sessions either side of the cadence change". Every measurement break in this suite is a
 * date, and results either side of one must never be pooled — so a log that can only be filtered
 * by string prefix cannot be pointed at one side of a break, which is the question the log most
 * needs to answer.
 */

let config: ConsoleConfig;
const SYM = CURRENT_ERA.symbol;
const DAYS = ["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25"];

function seed(dir: string): void {
  fs.mkdirSync(dir, { recursive: true });
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(`
    CREATE TABLE fly_positions (
      id INTEGER PRIMARY KEY, trade_date TEXT, entry_time TEXT, symbol TEXT, arm TEXT,
      entry_mode TEXT, kind TEXT, side TEXT, center REAL, entry_window TEXT,
      net REAL, fees REAL, pnl REAL, completion_latency_min REAL, pinned INTEGER,
      status TEXT, void_reason TEXT
    );
  `);
  const ins = db.prepare(
    `INSERT INTO fly_positions (trade_date, entry_time, symbol, arm, entry_mode, kind, side,
       center, entry_window, net, fees, pnl, completion_latency_min, pinned, status, void_reason)
     VALUES (?, '10:00', ?, 'control', 'legged', 'fly', 'put', 6000, 'am', 1.0, 0.5, ?, 5, 0, 'settled', NULL)`,
  );
  DAYS.forEach((d, i) => ins.run(d, SYM, i + 1));
  db.close();
}

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-flies-range-"));
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

const log = (over: Partial<typeof NO_TRADE_LOG_QUERY> = {}) =>
  readFliesTradeLog(config, "paper", { ...NO_TRADE_LOG_QUERY, era: "ALL", ...over });

describe("the trade log's date bounds", () => {
  it("serves every session when both bounds are absent", () => {
    expect(log().total).toBe(DAYS.length);
  });

  it("bounds each side inclusively", () => {
    expect(log({ from: "2026-08-21" }).rows.map((r) => r.tradeDate)).toEqual([
      "2026-08-25",
      "2026-08-24",
      "2026-08-21",
    ]);
    expect(log({ to: "2026-08-21" }).rows.map((r) => r.tradeDate)).toEqual([
      "2026-08-21",
      "2026-08-20",
    ]);
  });

  it("narrows to one side of a measurement break when both are given", () => {
    const r = log({ from: "2026-08-21", to: "2026-08-24" });
    expect(r.rows.map((x) => x.tradeDate)).toEqual(["2026-08-24", "2026-08-21"]);
    // The count describes the SCOPE, not the page — same contract as every other filter here.
    expect(r.total).toBe(2);
  });

  it("returns nothing for an inverted range rather than ignoring a bound", () => {
    expect(log({ from: "2026-08-25", to: "2026-08-20" }).total).toBe(0);
  });
});
