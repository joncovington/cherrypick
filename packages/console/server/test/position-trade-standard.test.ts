import { beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readCurveHistory } from "../src/readers/curve.js";
import { positionCash } from "../src/readers/positionCash.js";

/**
 * The trade table standard (root CLAUDE.md) for the position modules that share one accounting --
 * calendars, pmcc and curve -- through `readers/positionCash.ts`, exercised here via curve's history.
 *
 * `fees` is the total of every cost (entry fee and slippage, each exit's fee and slippage, and the
 * settlement and assignment charges, `settlement_fees` a component of it). A row must add up --
 * entry + exit = gross, gross − fees − settlement − slippage = net -- which takes each part out of
 * the total once. Every assertion is on a row that must exist: `withReadOnlyDb` turns a missing
 * column into an empty page, which must fail here rather than pass.
 */

let config: ConsoleConfig;

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-position-standard-"));
  fs.mkdirSync(path.join(tmp, "curve"));
  const db = new Database(path.join(tmp, "curve", "paper_trades.db"));
  db.exec(`
    CREATE TABLE curve_positions (
      id INTEGER PRIMARY KEY AUTOINCREMENT, position_id TEXT, symbol TEXT, arm TEXT, entry_session TEXT,
      closed_session TEXT, status TEXT, exit_reason TEXT, short_strike REAL, long_strike REAL,
      expiration TEXT, entry_spot REAL, settlement_spot REAL, entry_credit REAL, entry_width REAL,
      entry_ratio REAL, entry_regime TEXT, entry_hook INTEGER, gross_pnl REAL, fees REAL, quantity INTEGER,
      entry_slippage REAL, exit_slippage REAL, settlement_fees REAL, itm_settlements INTEGER
    );
    CREATE TABLE curve_assignments (id INTEGER PRIMARY KEY, position_id TEXT, fees REAL, status TEXT);
  `);
  const ins = db.prepare(
    `INSERT INTO curve_positions (position_id, symbol, arm, entry_session, closed_session, status, exit_reason,
       short_strike, long_strike, expiration, entry_credit, gross_pnl, fees, quantity, entry_slippage,
       exit_slippage, settlement_fees, itm_settlements)
     VALUES (?, 'VXX', ?, '2026-09-01', '2026-09-20', 'closed', ?, 50, 55, '2026-10-16', ?, ?, ?, ?, ?, ?, ?, ?)`,
  );
  // Profit take: 1.00 cr x2 = +200 entry, gross 120 so exit -80; fees 3.00 + 0.80 slip + 1.20 exit
  // fee + 0.60 exit slip = 5.60.
  ins.run("c-pt", "control", "profit_take", 1.0, 120, 5.6, 2, 0.8, 0.6, 0, 0);
  // Assigned through both strikes: the disposal charges are the settlement column.
  ins.run("c-asg", "hook", "shares_disposed", 1.1, -390, 3.0 + 0.4 + 10.24, 1, 0.4, 0, 10.24, 2);
  // Expired worthless, split never recorded.
  ins.run("c-exp", "noflip", "expired", 0.9, 90, 3.44, 1, 0.3, null, null, 0);
  db.prepare("INSERT INTO curve_assignments (position_id, fees, status) VALUES ('c-asg', 5.12, 'disposed'), ('c-asg', 5.12, 'disposed')").run();
  db.prepare(
    `INSERT INTO curve_positions (position_id, symbol, arm, entry_session, status, entry_credit, quantity, fees)
     VALUES ('c-open', 'VXX', 'control', '2026-09-20', 'open', 1.0, 1, 3.0)`,
  ).run();
  db.close();
  config = { paths: { curveDir: path.join(tmp, "curve") } } as unknown as ConsoleConfig;
});

const history = () => readCurveHistory(config, { arm: null, symbol: null });
const row = (id: string) => history().rows.find((r) => r.positionId === id);

describe("a position module's completed rows", () => {
  it("are the closed positions only", () => {
    expect(history().total).toBe(3);
  });

  it("sign the entry in whole-position dollars and derive the exit so the row adds up", () => {
    expect(row("c-pt")?.entryCash).toBe(200);
    expect(row("c-pt")?.exitCash).toBe(-80);
  });

  it("add up: entry + exit = gross, gross - fees - settlement - slippage = net", () => {
    for (const r of history().rows) {
      expect((r.entryCash ?? 0) + (r.exitCash ?? 0)).toBeCloseTo(r.grossPnl ?? NaN, 2);
      expect((r.grossPnl ?? 0) - (r.fees ?? 0) - (r.settlementFees ?? 0) - (r.slippage ?? 0)).toBeCloseTo(r.netPnl ?? NaN, 2);
    }
  });

  it("take each part out of the fee total once", () => {
    expect(row("c-pt")?.slippage).toBe(1.4);
    expect(row("c-pt")?.fees).toBe(4.2);
    expect(row("c-asg")?.settlementFees).toBe(10.24);
    expect(row("c-asg")?.fees).toBe(3);
  });

  it("leave settlement in the fees where the split was never recorded", () => {
    expect(row("c-exp")?.settlementFees).toBeNull();
    expect(row("c-exp")?.fees).toBe(3.14);
  });

  it("name how each ended, assignment included", () => {
    expect(row("c-pt")?.exitKind).toBe("closed");
    expect(row("c-asg")?.exitKind).toBe("assigned");
    expect(row("c-exp")?.exitKind).toBe("expired");
  });

  it("total the same way the rows add up", () => {
    const t = history().totals;
    expect(t.positions).toBe(3);
    expect(t.gross).toBe(-180);
    expect(t.gross - t.fees - t.settlementFees - t.slippage).toBeCloseTo(t.net, 2);
  });
});

describe("positionCash", () => {
  it("signs a debit structure's entry negative", () => {
    const c = positionCash({ status: "closed", quantity: 2, gross_pnl: 50, fees: 4 }, -1.5);
    expect(c.entryCash).toBe(-300);
    expect(c.exitCash).toBe(350);
    expect(c.netPnl).toBe(46);
  });

  it("gives an open position an entry and nothing on the exit side", () => {
    const c = positionCash({ status: "open", quantity: 1, gross_pnl: null, fees: 3 }, 1);
    expect(c.exitCash).toBeNull();
    expect(c.exitKind).toBeNull();
    expect(c.netPnl).toBeNull();
  });
});
