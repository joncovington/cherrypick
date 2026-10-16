import { beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import Fastify from "fastify";
import type { ConsoleConfig } from "../src/config.js";
import { readMeic, NO_TRADE_QUERY, MEIC_TRADE_SORTS } from "../src/readers/meic.js";
import { readFliesTradeLog, NO_TRADE_LOG_QUERY, TRADE_LOG_SORTS } from "../src/readers/flies.js";
import { readEarnings, EARNINGS_HISTORY_SORTS } from "../src/readers/earnings.js";
import { FIRST_PAGE } from "../src/readers/paging.js";
import { registerSecurity } from "../src/security.js";
import { registerModuleRoutes } from "../src/routes/modules.js";

/**
 * The server-sorted history tables (2026-10-16): MEIC's trade log, the flies trade log and the
 * earnings history. Each fixture puts its best trades OLDEST, so the reader's default first page
 * (newest first) holds none of them: a sorted first page that only re-ordered the default page
 * would come back with the wrong rows, and these tests would say so. Shown to fail by dropping the
 * sort accessors from each reader.
 *
 * The web tables name the columns they offer to sort in their own lists; the last block holds each
 * list to the reader's accessors, so a header can never offer a sort the server ignores.
 */

const MEIC_SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "meic-ledger-schema.sql"), "utf-8");
const EARNINGS_SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "earnings-ledger-schema.sql"), "utf-8");
const DAYS = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"];
// Oldest best: the default newest-first page of two is the two worst.
const GROSS = [500, 400, -100, -200, -300];
let tmp: string;
let config: ConsoleConfig;

beforeAll(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-history-sort-"));

  fs.mkdirSync(path.join(tmp, "meic"));
  const meic = new Database(path.join(tmp, "meic", "paper_trades.db"));
  meic.exec(MEIC_SCHEMA);
  const ic = meic.prepare(
    `INSERT INTO ic_trades (trade_date, symbol, ic_order_id, status, arm, era, put_strike, call_strike,
       wing_width, net_credit, quantity, pnl, fees, settlement_fees, slippage_dollars, exit_reason,
       put_settle_value, call_settle_value, entry_time, created_at, updated_at)
     VALUES (?, 'SPX', ?, 'expired', 'a', 'advisor', 5950, 6050, 10, 0.5, 1, ?, 2, 0, 1, 'expired_settlement',
             0, 0, ?, ?, ?)`,
  );
  DAYS.forEach((d, i) => {
    const at = `${d}T10:00:00-04:00`;
    ic.run(d, `ic-${i}`, GROSS[i], at, at, at);
  });
  meic.close();

  fs.mkdirSync(path.join(tmp, "flies"));
  const flies = new Database(path.join(tmp, "flies", "paper_trades.db"));
  flies.exec(`
    CREATE TABLE fly_positions (
      id INTEGER PRIMARY KEY, trade_date TEXT, entry_time TEXT, symbol TEXT, arm TEXT,
      entry_mode TEXT, kind TEXT, side TEXT, center REAL, wing_width REAL, far_width REAL,
      entry_window TEXT, quantity INTEGER DEFAULT 1,
      net REAL, fees REAL, pnl REAL, gross_pnl REAL, completion_latency_min REAL, pinned INTEGER,
      status TEXT, void_reason TEXT
    );
  `);
  const fly = flies.prepare(
    `INSERT INTO fly_positions (trade_date, entry_time, symbol, arm, entry_mode, kind, side, center,
       wing_width, entry_window, net, fees, pnl, gross_pnl, completion_latency_min, pinned, status)
     VALUES (?, ? || 'T10:00:00-04:00', 'SPX', 'control', 'legged', 'fly', 'put', 6000, 5, 'am',
             1.0, 1, ?, ?, 5, 0, 'settled')`,
  );
  DAYS.forEach((d, i) => fly.run(d, d, GROSS[i]! - 1, GROSS[i]));
  flies.close();

  fs.mkdirSync(path.join(tmp, "earnings"));
  const earnings = new Database(path.join(tmp, "earnings", "paper_trades.db"));
  earnings.exec(EARNINGS_SCHEMA);
  const trade = earnings.prepare(
    `INSERT INTO trades (order_id, strategy, symbol, expiration, entry_credit, exit_debit, pnl, opened_at,
       closed_at, quantity, entry_cost, exit_cost, entry_slippage, exit_slippage, settlement_fees, status,
       exit_reason, capital_at_risk)
     VALUES (?, 'iron_fly', 'ZM', '2026-09-26', 1.0, 0.5, ?, ?, ?, 1, 1, 1, 0, 0, 0, 'closed', 'profit_target', 500)`,
  );
  // Opened a day apart in the same order as the others: oldest best.
  GROSS.forEach((g, i) => trade.run(`t-${i}`, g, 1789000000 + i * 86400, 1789000000 + i * 86400 + 3600));
  earnings.close();

  config = {
    port: 0,
    paths: {
      cherrypick: tmp,
      meicDir: path.join(tmp, "meic"),
      fliesDir: path.join(tmp, "flies"),
      earningsDir: path.join(tmp, "earnings"),
      orchestratorConfig: path.join(tmp, "none.json"),
    },
  } as unknown as ConsoleConfig;
});

const meicHistory = (over: Partial<typeof NO_TRADE_QUERY> = {}) =>
  readMeic(config, "paper", { ...NO_TRADE_QUERY, era: "ALL", view: "history", from: "2026-09-01", limit: 2, ...over }).trades;

describe("MEIC's trade log sorts on the server", () => {
  it("defaults to newest first, which here is the two worst", () => {
    expect(meicHistory().rows.map((r) => r.gross)).toEqual([-300, -200]);
  });

  it("serves the WHOLE log's best on the first sorted page, not the default page re-ordered", () => {
    const p = meicHistory({ sort: "net", dir: "desc" });
    expect(p.rows.map((r) => r.gross)).toEqual([500, 400]);
    expect(p.total).toBe(5);
  });

  it("sorts by the net the row serves (gross − fees), and pages the sorted set", () => {
    expect(meicHistory({ sort: "net", dir: "asc" }).rows.map((r) => r.net)).toEqual([-302, -202]);
    expect(meicHistory({ sort: "net", dir: "desc", offset: 2 }).rows.map((r) => r.gross)).toEqual([-100, -200]);
  });

  it("orders by date in either direction", () => {
    expect(meicHistory({ sort: "date", dir: "asc" }).rows.map((r) => r.tradeDate)).toEqual(DAYS.slice(0, 2));
  });

  it("ignores a column it does not name, keeping its own order", () => {
    expect(meicHistory({ sort: "reason", dir: "asc" }).rows.map((r) => r.gross)).toEqual([-300, -200]);
  });

  it("reaches the reader through the route's query string", async () => {
    const app = Fastify();
    registerSecurity(app);
    registerModuleRoutes(app, config);
    await app.ready();
    const res = await app.inject({
      method: "GET",
      url: "/api/meic?mode=paper&view=history&era=ALL&from=2026-09-01&limit=2&offset=0&sort=gross&dir=desc",
    });
    expect(res.statusCode).toBe(200);
    expect((res.json() as { trades: { rows: Array<{ gross: number }> } }).trades.rows.map((r) => r.gross)).toEqual([500, 400]);
    await app.close();
  });
});

describe("the flies trade log sorts on the server", () => {
  const log = (over: Partial<typeof NO_TRADE_LOG_QUERY> = {}) =>
    readFliesTradeLog(config, "paper", { ...NO_TRADE_LOG_QUERY, era: "ALL", limit: 2, ...over });

  it("serves the whole log's best first, not the default page re-ordered", () => {
    expect(log().rows.map((r) => r.pnl)).toEqual([-301, -201]);
    const p = log({ sort: "net", dir: "desc" });
    expect(p.rows.map((r) => r.pnl)).toEqual([499, 399]);
    expect(p.total).toBe(5);
  });

  it("keeps its totals over every matching row whatever the sort", () => {
    expect(log({ sort: "gross", dir: "asc" }).totals.trades).toBe(5);
  });
});

describe("the earnings history sorts on the server", () => {
  const history = (sort?: string, dir?: "asc" | "desc") =>
    readEarnings(
      config,
      { trades: { limit: 2, offset: 0, ...(sort !== undefined ? { sort, dir } : {}) }, reviews: FIRST_PAGE },
      "ALL",
    ).trades;

  it("serves the whole history's best first, not the default page re-ordered", () => {
    expect(history().rows.map((r) => r.gross)).toEqual([-300, -200]);
    const p = history("net", "desc");
    expect(p.rows.map((r) => r.gross)).toEqual([500, 400]);
    expect(p.total).toBe(5);
  });

  it("orders by the open in either direction", () => {
    expect(history("opened", "asc").rows.map((r) => r.orderId)).toEqual(["t-0", "t-1"]);
  });
});

describe("each web table offers only the sorts its reader names", () => {
  const web = (rel: string) => fs.readFileSync(path.join(__dirname, "..", "..", "web", "src", "pages", rel), "utf-8");
  /** The string literals of the array assigned to `name` in a web source file. */
  const listIn = (src: string, name: string): string[] => {
    const m = new RegExp(`${name}[^=]*=\\s*\\[([^\\]]*)\\]`).exec(src);
    if (m === null) throw new Error(`${name} not found`);
    return [...m[1]!.matchAll(/"([^"]+)"/g)].map((x) => x[1]!);
  };

  it("MEIC", () => {
    expect(listIn(web("Meic/MeicTables.tsx"), "MEIC_TRADE_SORTS").sort()).toEqual(Object.keys(MEIC_TRADE_SORTS).sort());
  });

  it("flies", () => {
    expect(listIn(web("Flies/HistoryTab.tsx"), "TRADE_LOG_SORTS").sort()).toEqual(Object.keys(TRADE_LOG_SORTS).sort());
  });

  it("earnings", () => {
    expect(listIn(web("Earnings/EarningsHistory.tsx"), "HISTORY_SORTS").sort()).toEqual(
      Object.keys(EARNINGS_HISTORY_SORTS).sort(),
    );
  });
});
