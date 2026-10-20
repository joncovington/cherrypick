import { beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readEarningsLive } from "../src/readers/earningsLive.js";

/**
 * The earnings live card's open positions in the money layout (root CLAUDE.md): one per-share figure
 * per side, the rest whole-position dollars. The ledger stores `entry_credit` and a mark's
 * `exit_debit` as POSITION totals per share (strat_test_harness: per-contract x quantity), so a
 * multi-contract row passed straight through states the total under "per share". The schema is
 * earnings' own fixture.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "earnings-ledger-schema.sql"), "utf-8");
let config: ConsoleConfig;

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-earnings-live-"));
  fs.mkdirSync(path.join(tmp, "earnings"));
  const db = new Database(path.join(tmp, "earnings", "paper_trades.db"));
  db.exec(SCHEMA);
  // A 2-lot iron fly at 1.80 cr/sh: entry_credit 3.60. Marked at 0.65 db/sh to close: exit_debit 1.30,
  // unrealised (3.60 - 1.30) x 100 = +230 (management.unrealized_pnl).
  db.prepare(
    `INSERT INTO trades (order_id, strategy, symbol, expiration, entry_credit, opened_at, quantity, status, capital_at_risk)
     VALUES ('t-two', 'iron_fly', 'ZM', '2026-10-23', 3.6, 1789000000, 2, 'open', 640)`,
  ).run();
  db.prepare(
    `INSERT INTO position_marks (order_id, marked_at, session_date, exit_debit, unrealized_pnl, source, usable)
     VALUES ('t-two', 1789003600, '2026-10-19', 1.3, 230, 'stream', 1)`,
  ).run();
  db.close();
  config = { paths: { earningsDir: path.join(tmp, "earnings") } } as unknown as ConsoleConfig;
});

describe("earnings live positions", () => {
  it("state a 2-lot's price and mark per share, and its cash whole-position", () => {
    const p = readEarningsLive(config).positions.find((x) => x.orderId === "t-two")!;
    expect(p.quantity).toBe(2);
    expect(p.entryCredit).toBe(1.8);
    expect(p.entryCash).toBe(360);
    expect(p.mark?.exitDebit).toBe(0.65);
    expect(p.mark?.exitCash).toBe(-130);
    // The row adds up: entry + to-close = the unrealised gross the module recorded.
    expect(p.entryCash! + p.mark!.exitCash!).toBe(p.mark!.unrealizedPnl);
  });
});
