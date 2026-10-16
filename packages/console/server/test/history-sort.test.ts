import { beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { BWB_HISTORY_SORTS, readBwbHistory } from "../src/readers/bwb.js";
import { CURVE_HISTORY_SORTS, readCurveHistory } from "../src/readers/curve.js";
import { PMCC_HISTORY_SORTS, readPmccHistory } from "../src/readers/pmcc.js";

/**
 * Server-side sort on bwb's, curve's and pmcc's completed-position histories (2026-10-16).
 *
 * Each fixture holds five closed positions entered on consecutive days, so the default order
 * (newest entry first) puts the two largest nets -- the two OLDEST positions -- on the last page.
 * A sorted first page must be those two: the top of the whole history, not the default page
 * re-ordered. Each reader's sort ids must also be the ones its web table offers as buttons.
 */

const WEB = path.join(__dirname, "..", "..", "web", "src", "pages");

/** The id list a history tab declares beside its columns, read from its source. */
function webSorts(page: string, name: string): string[] {
  const src = fs.readFileSync(path.join(WEB, page, "HistoryTab.tsx"), "utf-8");
  const list = new RegExp(`const ${name} = \\[([^\\]]*)\\]`).exec(src);
  if (list === null) throw new Error(`${name} not found in ${page}/HistoryTab.tsx`);
  return [...list[1]!.matchAll(/"([^"]+)"/g)].map((m) => m[1]!).sort();
}

// [id, entry session, gross, fee total, settlement]. Net = gross - fee total. p3 carries the
// largest fee TOTAL but nearly all of it is settlement, so its fee column is the smallest.
const ROWS: Array<[string, string, number, number, number]> = [
  ["p1", "2026-09-01", 500, 5, 0],
  ["p2", "2026-09-02", 400, 5, 0],
  ["p3", "2026-09-03", 10, 50, 48],
  ["p4", "2026-09-04", 20, 8, 0],
  ["p5", "2026-09-05", 30, 5, 0],
];
const ids = (rows: Array<{ positionId: string }>) => rows.map((r) => r.positionId);

let tmp: string;
beforeAll(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-history-sort-"));

  fs.mkdirSync(path.join(tmp, "bwb"));
  const bwb = new Database(path.join(tmp, "bwb", "paper_trades.db"));
  bwb.exec(`
    CREATE TABLE bwb_positions (
      id INTEGER PRIMARY KEY AUTOINCREMENT, position_id TEXT, symbol TEXT, arm TEXT,
      entry_session TEXT, closed_session TEXT, status TEXT, exit_reason TEXT, body_strike REAL,
      near_strike REAL, far_strike REAL, expiration TEXT, entry_spot REAL, entry_credit REAL,
      armed_at TEXT, addon_fired_at TEXT, addon_credit REAL, gross_pnl REAL, fees REAL,
      quantity INTEGER, itm_settlements INTEGER, entry_cost REAL, entry_slippage REAL,
      addon_cost REAL, addon_slippage REAL, settlement_fees REAL, fees_source TEXT,
      addon_short_strike REAL, addon_long_strike REAL
    );
  `);
  const insBwb = bwb.prepare(
    `INSERT INTO bwb_positions (position_id, symbol, arm, entry_session, closed_session, status, exit_reason,
       entry_credit, gross_pnl, fees, quantity, itm_settlements, settlement_fees)
     VALUES (?, 'SPX', 'control', ?, '2026-09-22', 'closed', 'expired', 0.85, ?, ?, 1, 0, ?)`,
  );
  for (const [id, session, gross, fees, settle] of ROWS) insBwb.run(id, session, gross, fees, settle);
  bwb.close();

  fs.mkdirSync(path.join(tmp, "curve"));
  const curve = new Database(path.join(tmp, "curve", "paper_trades.db"));
  curve.exec(`
    CREATE TABLE curve_positions (
      id INTEGER PRIMARY KEY AUTOINCREMENT, position_id TEXT, symbol TEXT, arm TEXT, entry_session TEXT,
      closed_session TEXT, status TEXT, exit_reason TEXT, short_strike REAL, long_strike REAL,
      expiration TEXT, entry_spot REAL, settlement_spot REAL, entry_credit REAL, entry_width REAL,
      entry_ratio REAL, entry_regime TEXT, entry_hook INTEGER, gross_pnl REAL, fees REAL, quantity INTEGER,
      entry_slippage REAL, exit_slippage REAL, settlement_fees REAL, itm_settlements INTEGER
    );
    CREATE TABLE curve_assignments (id INTEGER PRIMARY KEY, position_id TEXT, fees REAL, status TEXT);
  `);
  const insCurve = curve.prepare(
    `INSERT INTO curve_positions (position_id, symbol, arm, entry_session, closed_session, status, exit_reason,
       short_strike, long_strike, entry_credit, entry_ratio, gross_pnl, fees, quantity, entry_slippage,
       exit_slippage, settlement_fees, itm_settlements)
     VALUES (?, 'VXX', 'control', ?, '2026-09-22', 'closed', 'expired', 50, 52, 1.0, ?, ?, ?, 1, 0, 0, ?, 0)`,
  );
  // The oldest entry carries the highest ratio, so a ratio sort also has to reach the last page.
  ROWS.forEach(([id, session, gross, fees, settle], i) => insCurve.run(id, session, 0.99 - i * 0.01, gross, fees, settle));
  curve.close();

  fs.mkdirSync(path.join(tmp, "pmcc"));
  const pmcc = new Database(path.join(tmp, "pmcc", "paper_trades.db"));
  pmcc.exec(fs.readFileSync(path.join(__dirname, "fixtures", "pmcc-ledger-schema.sql"), "utf-8"));
  const insPmcc = pmcc.prepare(
    `INSERT INTO pmcc_positions (position_id, symbol, arm, entry_session, closed_session, quantity,
       long_expiration, long_strike, short_expiration, short_strike, net_debit, status, exit_reason,
       gross_pnl, fees, entry_cost, entry_slippage, exit_cost, exit_slippage, settlement_fees)
     VALUES (?, 'SLV', 'shield', ?, '2026-09-22', 1, '2027-09-17', 20, '2026-09-26', 30, 10, 'closed',
             'short_expiration', ?, ?, 0, 0, 0, 0, ?)`,
  );
  const insLeg = pmcc.prepare(
    `INSERT INTO pmcc_legs (position_id, leg_role, occ_symbol, streamer_symbol, expiration, strike,
       option_type, action, entry_bid, entry_ask, entry_mid)
     VALUES (?, 'long_call', 'X', 'X', '2027-09-17', 20, 'C', 'buy', ?, ?, 10)`,
  );
  // The entry spread is joined in per page; p1 has the widest, so sorting by it must see past page 1.
  ROWS.forEach(([id, session, gross, fees, settle], i) => {
    insPmcc.run(id, session, gross, fees, settle);
    insLeg.run(id, 10 - (5 - i) * 0.1, 10 + (5 - i) * 0.1);
  });
  pmcc.close();
});

const config = (): ConsoleConfig =>
  ({
    paths: { bwbDir: path.join(tmp, "bwb"), curveDir: path.join(tmp, "curve"), pmccDir: path.join(tmp, "pmcc") },
  }) as unknown as ConsoleConfig;
const all = { arm: null, symbol: null };

describe("bwb's history sorted on the server", () => {
  it("pages by entry, newest first, with no sort", () => {
    expect(ids(readBwbHistory(config(), all, { limit: 2, offset: 0 }).rows)).toEqual(["p5", "p4"]);
  });

  it("puts the history's two largest nets on the first page", () => {
    const page = readBwbHistory(config(), all, { limit: 2, offset: 0, sort: "net", dir: "desc" });
    expect(ids(page.rows)).toEqual(["p1", "p2"]);
    expect(page.total).toBe(5);
    expect(ids(readBwbHistory(config(), all, { limit: 2, offset: 2, sort: "net", dir: "desc" }).rows)).toEqual(["p5", "p4"]);
    expect(ids(readBwbHistory(config(), all, { limit: 2, offset: 0, sort: "net", dir: "asc" }).rows)).toEqual(["p3", "p4"]);
  });

  it("sorts fees as the column shows them, settlement taken out", () => {
    const page = readBwbHistory(config(), all, { limit: 5, offset: 0, sort: "fees", dir: "desc" });
    expect(ids(page.rows)[0]).toBe("p4");
    expect(ids(page.rows)[4]).toBe("p3");
  });

  it("keeps the default order for a column it does not name", () => {
    expect(ids(readBwbHistory(config(), all, { limit: 2, offset: 0, sort: "strikes", dir: "desc" }).rows)).toEqual(["p5", "p4"]);
  });

  it("names the same ids the web table offers", () => {
    expect(Object.keys(BWB_HISTORY_SORTS).sort()).toEqual(webSorts("Bwb", "BWB_HISTORY_SORTS"));
  });
});

describe("curve's history sorted on the server", () => {
  it("puts the history's two largest nets, and highest ratios, on the first page", () => {
    expect(ids(readCurveHistory(config(), all, { limit: 2, offset: 0 }).rows)).toEqual(["p5", "p4"]);
    expect(ids(readCurveHistory(config(), all, { limit: 2, offset: 0, sort: "net", dir: "desc" }).rows)).toEqual(["p1", "p2"]);
    expect(ids(readCurveHistory(config(), all, { limit: 2, offset: 0, sort: "regime", dir: "desc" }).rows)).toEqual(["p1", "p2"]);
  });

  it("names the same ids the web table offers", () => {
    expect(Object.keys(CURVE_HISTORY_SORTS).sort()).toEqual(webSorts("Curve", "CURVE_HISTORY_SORTS"));
  });
});

describe("pmcc's history sorted on the server", () => {
  it("puts the history's two largest nets on the first page", () => {
    expect(ids(readPmccHistory(config(), all, { limit: 2, offset: 0 }).rows)).toEqual(["p5", "p4"]);
    expect(ids(readPmccHistory(config(), all, { limit: 2, offset: 0, sort: "net", dir: "desc" }).rows)).toEqual(["p1", "p2"]);
  });

  it("sorts by entry spread over every cycle, not only the page it joins detail onto", () => {
    const page = readPmccHistory(config(), all, { limit: 2, offset: 0, sort: "spread", dir: "desc" });
    expect(ids(page.rows)).toEqual(["p1", "p2"]);
    expect(page.rows[0]?.entryMaxSpreadPct).toBeCloseTo(0.1, 6);
  });

  it("names the same ids the web table offers", () => {
    expect(Object.keys(PMCC_HISTORY_SORTS).sort()).toEqual(webSorts("Pmcc", "PMCC_HISTORY_SORTS"));
  });
});
