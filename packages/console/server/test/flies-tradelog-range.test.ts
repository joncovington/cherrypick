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
      entry_mode TEXT, kind TEXT, side TEXT, center REAL, wing_width REAL, far_width REAL,
      entry_window TEXT,
      net REAL, fees REAL, pnl REAL, gross_pnl REAL, completion_latency_min REAL, pinned INTEGER,
      status TEXT, void_reason TEXT
    );
  `);
  const ins = db.prepare(
    // entry_time is a full ISO stamp carrying the market's own offset, exactly as the module
    // records it -- the reader hands it on verbatim so the clock time is read off the string
    // rather than through a Date that would restate it in the viewer's timezone.
    `INSERT INTO fly_positions (trade_date, entry_time, symbol, arm, entry_mode, kind, side,
       center, wing_width, far_width, entry_window, net, fees, pnl, gross_pnl,
       completion_latency_min, pinned, status, void_reason)
     VALUES (?, ? || 'T10:00:00-04:00', ?, ?, 'legged', 'fly', 'put', 6000, 5, NULL, 'am',
             1.0, 0.5, ?, ?, 5, 0, 'settled', NULL)`,
  );
  // Two arms, and 2026-08-24 carries two trades so trades != sessions.
  DAYS.forEach((d, i) => ins.run(d, d, SYM, "control", i + 1, i + 1.5));
  ins.run("2026-08-24", "2026-08-24", SYM, "width-5", 10, 10.5);
  // The width-5 row carries a BROKEN wing, so the near/far pair is exercised without adding a row
  // the surrounding count assertions would have to be retuned for.
  db.prepare("UPDATE fly_positions SET far_width = 10 WHERE arm = 'width-5'").run();
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

// Scoped to one arm so these read one row per session — the fixture carries a second arm on
// 2026-08-24 so that `trades` and `sessions` can differ for the totals tests below.
const days = (over: Partial<typeof NO_TRADE_LOG_QUERY> = {}) => log({ arm: "control", ...over });

describe("the trade log's date bounds", () => {
  it("serves every session when both bounds are absent", () => {
    expect(days().total).toBe(DAYS.length);
  });

  it("bounds each side inclusively", () => {
    expect(days({ from: "2026-08-21" }).rows.map((r) => r.tradeDate)).toEqual([
      "2026-08-25",
      "2026-08-24",
      "2026-08-21",
    ]);
    expect(days({ to: "2026-08-21" }).rows.map((r) => r.tradeDate)).toEqual([
      "2026-08-21",
      "2026-08-20",
    ]);
  });

  it("narrows to one side of a measurement break when both are given", () => {
    const r = days({ from: "2026-08-21", to: "2026-08-24" });
    expect(r.rows.map((x) => x.tradeDate)).toEqual(["2026-08-24", "2026-08-21"]);
    // The count describes the SCOPE, not the page — same contract as every other filter here.
    expect(r.total).toBe(2);
  });

  it("returns nothing for an inverted range rather than ignoring a bound", () => {
    expect(log({ from: "2026-08-25", to: "2026-08-20" }).total).toBe(0);
  });
});

describe("the arm filter", () => {
  it("narrows to one arm", () => {
    expect(log().total).toBe(DAYS.length + 1);
    const only = log({ arm: "width-5" });
    expect(only.total).toBe(1);
    expect(only.rows.every((r) => r.arm === "width-5")).toBe(true);
  });

  it("composes with the date bounds rather than replacing them", () => {
    // 2026-08-24 holds one row per arm, so the two filters have to intersect.
    const r = log({ arm: "control", from: "2026-08-24", to: "2026-08-24" });
    expect(r.total).toBe(1);
    expect(r.rows[0]?.arm).toBe("control");
  });
});

describe("the log's totals", () => {
  it("describes every matching row, not the rendered page", () => {
    // A total over 2 of 5 matching trades is not a total; the page size is a viewport accident.
    const r = log({ limit: 2 });
    expect(r.rows).toHaveLength(2);
    expect(r.totals.trades).toBe(5);
    expect(r.totals.netPnl).toBe(1 + 2 + 3 + 4 + 10);
  });

  it("reports sessions beside trades", () => {
    // Same-day trades share a regime and are not independent observations. 2026-08-24 carries two.
    const r = log();
    expect(r.totals.trades).toBe(5);
    expect(r.totals.sessions).toBe(4);
  });

  it("follows the filters it is shown beside", () => {
    const r = log({ arm: "width-5" });
    expect(r.totals.trades).toBe(1);
    expect(r.totals.netPnl).toBe(10);
  });

  it("separates net from gross, and carries the fees between them", () => {
    const r = log();
    expect(r.totals.grossPnl).toBeCloseTo(1.5 + 2.5 + 3.5 + 4.5 + 10.5, 5);
    expect(r.totals.fees).toBeCloseTo(0.5 * 5, 5);
    expect(r.totals.netPnl).toBeLessThan(r.totals.grossPnl);
  });

  it("is zeroed, not absent, when nothing matches", () => {
    const r = log({ from: "2026-08-25", to: "2026-08-20" });
    expect(r.total).toBe(0);
    expect(r.totals).toEqual({ trades: 0, sessions: 0, netPnl: 0, grossPnl: 0, fees: 0 });
  });
});

describe("the geometry and clock a row carries", () => {
  it("hands the entry stamp on verbatim, offset and all", () => {
    // Deliberately the stored string rather than a parsed time: it carries the market's own offset,
    // and re-rendering it through a Date would restate a 10:00 SPX entry in the viewer's timezone --
    // a session-relative fact reported in a session that never happened.
    expect(days().rows[0]?.entryTime).toBe("2026-08-25T10:00:00-04:00");
  });

  it("reports the wing width, and leaves the far side null when the wing is symmetric", () => {
    const r = days().rows[0];
    expect(r?.wingWidth).toBe(5);
    expect(r?.farWidth).toBeNull();
  });

  it("keeps a broken wing's two widths apart", () => {
    // The asymmetry IS the trade in a broken wing; collapsing the pair to one number would describe
    // a 5/10 as a 5-point fly, which is a different structure with a different risk profile.
    const r = log({ arm: "width-5" }).rows[0];
    expect(r?.wingWidth).toBe(5);
    expect(r?.farWidth).toBe(10);
  });
});
