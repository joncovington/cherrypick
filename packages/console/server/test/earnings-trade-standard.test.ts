import { beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readEarnings, readEarningsAnalytics, earningsExitKind } from "../src/readers/earnings.js";

/**
 * The trade table standard (root CLAUDE.md) on earnings' closed trades.
 *
 * earnings keeps costs out of `pnl` (gross) and in entry_cost + exit_cost, which carry the slippage
 * charge and, on exit_cost, any settlement fee (`settlement_fees`, 2026-09-25). A row must add up --
 * entry + exit = gross, gross − fees − settlement − slippage = net -- taking each part out of the
 * cost total once. The schema is earnings' own fixture, pinned by its test_console_schema_fixture.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "earnings-ledger-schema.sql"), "utf-8");
let config: ConsoleConfig;

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-earnings-standard-"));
  fs.mkdirSync(path.join(tmp, "earnings"));
  const db = new Database(path.join(tmp, "earnings", "paper_trades.db"));
  db.exec(SCHEMA);
  const ins = db.prepare(
    `INSERT INTO trades (order_id, strategy, symbol, expiration, entry_credit, exit_debit, pnl, opened_at,
       closed_at, quantity, entry_cost, exit_cost, entry_slippage, exit_slippage, settlement_fees, status,
       exit_reason, capital_at_risk)
     VALUES (?, 'iron_fly', ?, '2026-09-19', ?, ?, ?, 1789000000, ?, ?, ?, ?, ?, ?, ?, ?, ?, 500)`,
  );
  // Settled with one strike ITM: 2.00 cr x2 = +400, exit debit 3.00 x2 -> gross -200, exit -600;
  // entry_cost 3.00 (1.00 slippage), exit_cost 5.00 = the $5 settlement fee.
  ins.run("t-itm", "ZM", 2.0, 3.0, -200, 1789300000, 2, 3.0, 5.0, 1.0, 0, 5.0, "closed", "expired");
  // Traded out at a profit target.
  ins.run("t-pt", "AEO", 1.5, 0.5, 100, 1789100000, 1, 2.5, 2.2, 0.6, 0.4, 0, "closed", "profit_target");
  // A debit structure, closed before the split was recorded.
  ins.run("t-old", "INTU", -1.2, -1.6, 40, 1789200000, 1, 1.4, 1.6, 0.3, 0.3, null, "closed", "front_expiry");
  // Still open: a position, not history.
  ins.run("t-open", "S", 0.9, null, null, null, 3, 2.1, null, 0.5, null, null, "open", null);
  db.close();
  config = {
    paths: { earningsDir: path.join(tmp, "earnings"), orchestratorConfig: path.join(tmp, "none.json") },
  } as unknown as ConsoleConfig;
});

const history = () => readEarnings(config, undefined, "ALL");
const row = (id: string) => history().trades.rows.find((r) => r.orderId === id);

describe("earnings' history rows", () => {
  it("are the closed trades only", () => {
    expect(history().trades.total).toBe(3);
  });

  it("sign the entry and exit in whole-position dollars, a debit entry negative", () => {
    expect(row("t-itm")?.entryCash).toBe(400);
    expect(row("t-itm")?.exitCash).toBe(-600);
    expect(row("t-old")?.entryCash).toBe(-120);
  });

  it("add up: entry + exit = gross, gross - fees - settlement - slippage = net", () => {
    for (const r of history().trades.rows) {
      expect((r.entryCash ?? 0) + (r.exitCash ?? 0)).toBeCloseTo(r.gross ?? NaN, 2);
      expect((r.gross ?? 0) - (r.fees ?? 0) - (r.settlementFees ?? 0) - (r.slippage ?? 0)).toBeCloseTo(r.net ?? NaN, 2);
    }
  });

  it("take each part out of the cost total once", () => {
    const r = row("t-itm");
    expect(r?.settlementFees).toBe(5);
    expect(r?.slippage).toBe(1);
    expect(r?.fees).toBe(2);
    expect(r?.net).toBe(-208);
  });

  it("read settlement as not recorded where the split never was", () => {
    expect(row("t-old")?.settlementFees).toBeNull();
    expect(row("t-old")?.fees).toBe(2.4);
  });

  it("name how each ended", () => {
    expect(row("t-itm")?.exitKind).toBe("settled");
    expect(row("t-pt")?.exitKind).toBe("closed");
    expect(earningsExitKind("expired", 0)).toBe("expired");
    expect(earningsExitKind("front_expiry", null)).toBe("closed");
  });

  it("total the same way the rows add up", () => {
    const t = history().totals;
    expect(t.trades).toBe(3);
    expect(t.gross).toBe(-60);
    expect(t.gross - t.fees - t.settlementFees - t.slippage).toBeCloseTo(t.net, 2);
  });
});

describe("earnings' open positions", () => {
  it("carry entry cash for every contract", () => {
    const open = readEarningsAnalytics(config, "paper", "ALL").openPositions.find((p) => p.symbol === "S");
    expect(open?.price).toBe(0.9);
    expect(open?.credit).toBe(270);
  });
});
