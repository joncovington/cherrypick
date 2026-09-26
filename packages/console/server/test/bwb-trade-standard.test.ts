import { beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readBwbHistory, bwbExitKind } from "../src/readers/bwb.js";

/**
 * The trade table standard (root CLAUDE.md) on bwb's completed positions.
 *
 * bwb is the other slippage model from flies and meic: fills at mid, slippage CHARGED as a cost and
 * folded into the `fees` total with the entry fee, the add-on fee and settlement. A row must still
 * add up -- entry + exit = gross, gross − fees − settlement − slippage = net -- which takes each
 * part out of the total exactly once. On a broker-reconciled row the real fills already carry the
 * slippage, so none is taken out there.
 *
 * `withReadOnlyDb` turns a missing column into an empty page, so every test here asserts on rows
 * that must exist: an empty payload fails rather than passing vacuously.
 */

let config: ConsoleConfig;

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-bwb-standard-"));
  fs.mkdirSync(path.join(tmp, "bwb"));
  const db = new Database(path.join(tmp, "bwb", "paper_trades.db"));
  db.exec(`
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
  const ins = db.prepare(
    `INSERT INTO bwb_positions (position_id, symbol, arm, entry_session, closed_session, status,
       exit_reason, body_strike, near_strike, far_strike, expiration, entry_credit, addon_fired_at,
       addon_credit, gross_pnl, fees, quantity, itm_settlements, entry_cost, entry_slippage,
       addon_cost, addon_slippage, settlement_fees, fees_source)
     VALUES (?, 'SPX', ?, '2026-09-15', '2026-09-22', 'closed', ?, 6600, 6620, 6560, '2026-09-22',
             ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)`,
  );
  // Settled through the body with the add-on fired: entry 0.85 x100 = +85, add-on 0.40 x100 = +40,
  // gross -300 so exit -425; fees 3.44 + 1.20 slip + 2.30 add-on + 0.80 add-on slip + 10 settlement = 17.74.
  ins.run("p-itm", "delta", "expired", 0.85, "2026-09-18T11:00", 0.4, -300, 17.74, 2, 3.44, 1.2, 2.3, 0.8, 10, null);
  db.prepare("UPDATE bwb_positions SET addon_short_strike = 6590, addon_long_strike = 6570 WHERE position_id = 'p-itm'").run();
  // Expired worthless, never fired: keeps its credit.
  ins.run("p-otm", "control", "expired", 0.85, null, null, 85, 4.64, 0, 3.44, 1.2, null, null, 0, null);
  // Broker-reconciled: fees is the broker's total, slippage is inside the real fills.
  ins.run("p-rec", "control", "expired", 0.9, null, null, 90, 8.0, 0, 3.44, 1.2, null, null, 0, "reconciled");
  // Split never recorded (a paper row: fees_source NULL).
  ins.run("p-old", "flip", "expired", 0.8, null, null, 80, 9.64, 1, 3.44, 1.2, null, null, null, null);
  // A live row before reconciliation: 1.20 of MEASURED slippage that was never charged into fees.
  ins.run("p-live", "control", "expired", 0.85, null, null, 85, 4.64, 0, 3.44, 1.2, null, null, 0, "broker_estimate");
  // Still open: not history.
  db.prepare(
    `INSERT INTO bwb_positions (position_id, symbol, arm, entry_session, status, entry_credit, quantity, fees)
     VALUES ('p-open', 'SPX', 'control', '2026-09-24', 'open', 0.85, 1, 4.64)`,
  ).run();
  db.close();
  config = { paths: { bwbDir: path.join(tmp, "bwb") } } as unknown as ConsoleConfig;
});

const history = () => readBwbHistory(config, { arm: null, symbol: null });
const row = (id: string) => history().rows.find((r) => r.positionId === id);

describe("bwb's completed positions", () => {
  it("are the closed ones only", () => {
    expect(history().total).toBe(5);
  });

  it("sign the fly's entry and the add-on's as separate whole-position cash flows", () => {
    // 0.85 fly credit and 0.40 add-on credit, one lot.
    expect(row("p-itm")?.entryCash).toBe(85);
    expect(row("p-itm")?.addOnCash).toBe(40);
    expect(row("p-itm")?.exitCash).toBe(-425);
  });

  it("carry the add-on's strikes, and leave the add-on blank where it never fired", () => {
    expect(row("p-itm")?.addonShortStrike).toBe(6590);
    expect(row("p-itm")?.addonLongStrike).toBe(6570);
    expect(row("p-otm")?.addonShortStrike).toBeNull();
    expect(row("p-otm")?.addOnCash).toBeNull();
    expect(row("p-otm")?.entryCash).toBe(85);
  });

  it("add up: entry + add-on + exit = gross, gross - fees - settlement - slippage = net", () => {
    for (const r of history().rows) {
      expect((r.entryCash ?? 0) + (r.addOnCash ?? 0) + (r.exitCash ?? 0)).toBeCloseTo(r.grossPnl ?? NaN, 2);
      expect((r.grossPnl ?? 0) - (r.fees ?? 0) - (r.settlementFees ?? 0) - (r.slippage ?? 0)).toBeCloseTo(r.netPnl ?? NaN, 2);
    }
  });

  it("take each part out of the fee total once", () => {
    const r = row("p-itm");
    expect(r?.slippage).toBe(2);
    expect(r?.settlementFees).toBe(10);
    expect(r?.fees).toBe(5.74);
    expect(r?.netPnl).toBe(-317.74);
  });

  it("take no slippage out of a live row's total, reconciled or not", () => {
    expect(row("p-rec")?.slippage).toBeNull();
    expect(row("p-rec")?.fees).toBe(8);
    expect(row("p-live")?.slippage).toBeNull();
    expect(row("p-live")?.fees).toBe(4.64);
  });

  it("leave settlement in the fees where the split was never recorded", () => {
    expect(row("p-old")?.settlementFees).toBeNull();
    expect(row("p-old")?.fees).toBe(8.44);
  });

  it("name how each ended", () => {
    expect(row("p-itm")?.exitKind).toBe("settled");
    expect(row("p-otm")?.exitKind).toBe("expired");
    expect(bwbExitKind("no_expiration_plan", 0)).toBe("closed");
  });

  it("total the same way the rows add up", () => {
    const t = history().totals;
    expect(t.positions).toBe(5);
    expect(t.gross).toBe(40);
    expect(t.net).toBeCloseTo(40 - 17.74 - 4.64 - 8 - 9.64 - 4.64, 2);
    expect(t.gross - t.fees - t.settlementFees - t.slippage).toBeCloseTo(t.net, 2);
  });
});

describe("bwb's history date range", () => {
  // Every closed fixture position closed on 2026-09-22; the range bounds the CLOSE.
  const ranged = (from: string | null, to: string | null) =>
    readBwbHistory(config, { arm: null, symbol: null, range: { from, to } });

  it("keeps the positions that closed inside it, totals included", () => {
    expect(ranged("2026-09-22", "2026-09-22").total).toBe(history().total);
    expect(ranged("2026-09-22", null).totals.net).toBe(history().totals.net);
  });

  it("drops the ones that closed outside it, from the rows and the totals alike", () => {
    const r = ranged(null, "2026-09-21");
    expect(r.total).toBe(0);
    expect(r.totals.positions).toBe(0);
    expect(ranged("2026-09-23", null).total).toBe(0);
  });
});
