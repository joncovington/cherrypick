import { beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readPmccHistory } from "../src/readers/pmcc.js";

/**
 * The trade table standard (root CLAUDE.md) on pmcc's completed cycles, against pmcc's own schema
 * (`fixtures/pmcc-ledger-schema.sql`, generated from its `db.connect` and pinned by its
 * test_console_schema_fixture.py).
 *
 * pmcc buys a diagonal for a net DEBIT, so the entry is money paid and signs negative. A cycle whose
 * short settled in the money is listed while its delivered shares await cover, but its result is
 * not final, so it is not summed. The detail's fee breakdown still splits the fee TOTAL, which the
 * standard's `fees` column no longer is -- `feesTotal` carries it.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "pmcc-ledger-schema.sql"), "utf-8");
let config: ConsoleConfig;

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-pmcc-standard-"));
  fs.mkdirSync(path.join(tmp, "pmcc"));
  const db = new Database(path.join(tmp, "pmcc", "paper_trades.db"));
  db.exec(SCHEMA);
  const ins = db.prepare(
    `INSERT INTO pmcc_positions (position_id, symbol, arm, entry_session, closed_session, quantity,
       long_expiration, long_strike, short_expiration, short_strike, net_debit, status, exit_reason,
       gross_pnl, fees, entry_cost, entry_slippage, exit_cost, exit_slippage, settlement_fees)
     VALUES (?, 'TQQQ', 'control', '2026-09-08', ?, 1, '2026-12-18', 60, '2026-09-19', 90, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  );
  // 25.00 db = -2500 entry; gross +140 so exit +2640; fees 1.30 + 6.00 slip + 1.30 + 5.40 slip = 14.00.
  ins.run("p-done", "2026-09-19", 25.0, "closed", "short_expiration", 140, 14.0, 1.3, 6.0, 1.3, 5.4, 0);
  // Short settled ITM, shares awaiting cover: listed, not summed.
  ins.run("p-wait", null, 24.0, "short_settled", null, null, 7.3, 1.3, 6.0, 0, 0, null);
  db.close();
  config = { paths: { pmccDir: path.join(tmp, "pmcc") } } as unknown as ConsoleConfig;
});

const history = () => readPmccHistory(config, { arm: null, symbol: null });
const row = (id: string) => history().rows.find((r) => r.positionId === id);

describe("pmcc's completed cycles", () => {
  it("list a cycle awaiting disposal beside the completed ones", () => {
    expect(history().total).toBe(2);
  });

  it("sign the debit entry negative and derive the exit so the row adds up", () => {
    const r = row("p-done");
    expect(r?.price).toBe(-25);
    expect(r?.entryCash).toBe(-2500);
    expect(r?.exitCash).toBe(2640);
    expect((r?.grossPnl ?? 0) - (r?.fees ?? 0) - (r?.settlementFees ?? 0) - (r?.slippage ?? 0)).toBeCloseTo(r?.netPnl ?? NaN, 2);
  });

  it("split the fee total into trading fees and slippage, keeping the total for the detail", () => {
    const r = row("p-done");
    expect(r?.slippage).toBe(11.4);
    expect(r?.fees).toBe(2.6);
    expect(r?.feesTotal).toBe(14);
  });

  it("leave an unfinished cycle without an exit or a net, and out of the totals", () => {
    const r = row("p-wait");
    expect(r?.exitCash).toBeNull();
    expect(r?.exitKind).toBeNull();
    expect(history().totals.positions).toBe(1);
    expect(history().totals.net).toBe(126);
  });
});

describe("pmcc's history date range", () => {
  it("keeps the cycle that closed inside it and drops one with no close yet", () => {
    const r = readPmccHistory(config, { arm: null, symbol: null, range: { from: "2026-09-19", to: null } });
    expect(r.rows.map((x) => x.positionId)).toEqual(["p-done"]);
    expect(r.totals.net).toBe(126);
  });
});
