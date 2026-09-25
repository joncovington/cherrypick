import { beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readCalendarsWeeks } from "../src/readers/calendars.js";

/**
 * The trade table standard (root CLAUDE.md) on calendars' weeks: every position of a week and arm
 * through the shared `positionCash`, summed, against calendars' own schema
 * (`fixtures/calendars-ledger-schema.sql`, pinned by its test_console_schema_fixture.py).
 *
 * A double calendar is bought for a debit, so a week's entry signs negative. A week does not finish
 * while any position -- delivered shares included -- is outstanding, so an unfinished week has no
 * exit and no net and is not summed: the closed half's number under the week's name would read as
 * the week's result.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "calendars-ledger-schema.sql"), "utf-8");
let config: ConsoleConfig;

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-calendars-standard-"));
  fs.mkdirSync(path.join(tmp, "calendars"));
  const db = new Database(path.join(tmp, "calendars", "paper_trades.db"));
  db.exec(SCHEMA);
  const ins = db.prepare(
    `INSERT INTO dc_positions (position_id, week_of, entry_session, arm, side, symbol, structure,
       front_expiration, back_expiration, strike, quantity, entry_debit, status, exit_reason,
       gross_pnl, fees, entry_slippage, exit_slippage, settlement_fees, itm_settlements)
     VALUES (?, ?, ?, 'control', ?, 'SPY', 'standard', '2026-09-04', '2026-09-11', ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  );
  // A finished week: call 2.00 db, put 1.80 db -> entry -380; gross +30 and -10 = +20, so exit +400.
  // fees 4.00 (1.00 slip) + 6.13 (0.80 slip, 5.00 settlement on the ITM put).
  ins.run("w1:call", "2026-08-31", "2026-08-31", "call", 650, 2.0, "closed", "scheduled_exit", 30, 4.0, 0.5, 0.5, 0, 0);
  ins.run("w1:put", "2026-08-31", "2026-08-31", "put", 640, 1.8, "closed", "long_disposition", -10, 6.13, 0.4, 0.4, 5.0, 1);
  // An unfinished week: one side still open.
  ins.run("w2:call", "2026-09-07", "2026-09-07", "call", 655, 2.1, "closed", "scheduled_exit", 15, 4.0, 0.5, 0.5, 0, 0);
  ins.run("w2:put", "2026-09-07", "2026-09-07", "put", 645, 1.9, "short_settled", null, null, 2.2, 0.5, 0, null, 1);
  db.close();
  config = { paths: { calendarsDir: path.join(tmp, "calendars") } } as unknown as ConsoleConfig;
});

const weeks = () => readCalendarsWeeks(config);
const week = (w: string) => weeks().rows.find((r) => r.weekOf === w);

describe("calendars' weeks", () => {
  it("are one row per week and arm", () => {
    expect(weeks().rows.map((r) => r.weekOf).sort()).toEqual(["2026-08-31", "2026-09-07"]);
  });

  it("sign a finished week's debit entry negative and add up", () => {
    const r = week("2026-08-31");
    expect(r?.price).toBe(-3.8);
    expect(r?.entryCash).toBe(-380);
    expect(r?.exitCash).toBe(400);
    expect(r?.grossPnl).toBe(20);
    expect((r?.grossPnl ?? 0) - (r?.fees ?? 0) - (r?.settlementFees ?? 0) - (r?.slippage ?? 0)).toBeCloseTo(r?.netPnl ?? NaN, 2);
    expect(r?.netPnl).toBe(9.87);
  });

  it("take settlement and slippage out of the fee total once", () => {
    const r = week("2026-08-31");
    expect(r?.settlementFees).toBe(5);
    expect(r?.slippage).toBe(1.8);
    expect(r?.fees).toBe(3.33);
  });

  it("name how a finished week ended, and leave an unfinished one without an exit or a net", () => {
    expect(week("2026-08-31")?.exitKind).toBe("settled");
    const open = week("2026-09-07");
    expect(open?.exitCash).toBeNull();
    expect(open?.exitKind).toBeNull();
    expect(open?.netPnl).toBeNull();
  });

  it("total the finished weeks only", () => {
    const t = weeks().totals;
    expect(t.positions).toBe(1);
    expect(t.net).toBe(9.87);
  });
});
