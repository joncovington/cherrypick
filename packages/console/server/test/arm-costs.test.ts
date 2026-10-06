import Database from "better-sqlite3";
import { describe, expect, it } from "vitest";
import { concededArmMoney, entryOutcomes } from "../src/readers/armCosts.js";

/**
 * The shared costs arithmetic. Where the modelled fill already concedes slippage (flies, meic) it is
 * inside gross: shown beside the row, never subtracted again, so gross - fees - settlement = net.
 */
describe("concededArmMoney", () => {
  it("splits the ledger's fee total and leaves slippage out of the subtraction", () => {
    const [arm] = concededArmMoney([
      { arm: "control", gross: 100, fees_total: 12, settlement_fees: 2, slippage_dollars: 6, premium: 300 },
      { arm: "control", gross: -40, fees_total: 8, settlement_fees: 0, slippage_dollars: 4, premium: 250 },
    ]);
    expect(arm).toMatchObject({ positions: 2, wins: 1, premium: 550, grossPnl: 60, fees: 18, settlementFees: 2, slippage: 10 });
    expect(arm!.netPnl).toBe(40); // 60 - 18 - 2: the 10 of slippage is already inside the 60
  });
});

describe("entryOutcomes", () => {
  it("counts sessions, and fills beside them for a module that enters many times a session", () => {
    const db = new Database(":memory:");
    db.exec("CREATE TABLE fly_entry_attempts (id INTEGER PRIMARY KEY, trade_date TEXT, arm TEXT, outcome TEXT)");
    const add = db.prepare("INSERT INTO fly_entry_attempts (trade_date, arm, outcome) VALUES (?, ?, ?)");
    for (const o of ["filled", "cadence_blocked", "filled", "filled"]) add.run("2026-10-06", "control", o);
    add.run("2026-10-07", "control", "gate_blocked");
    expect(entryOutcomes(db, "fly_entry_attempts", null)).toEqual([
      { arm: "control", sessions: 2, entered: 1, fills: 3, refusals: { gate_blocked: 1 } },
    ]);
  });
});
