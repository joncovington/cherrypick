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

  it("names an unentered session by its LAST attempt, by id, whatever order the index holds", () => {
    const db = new Database(":memory:");
    db.exec(`CREATE TABLE fly_entry_attempts (id INTEGER PRIMARY KEY, trade_date TEXT, arm TEXT, outcome TEXT);
             CREATE INDEX a ON fly_entry_attempts(arm, trade_date, outcome)`);
    const add = db.prepare("INSERT INTO fly_entry_attempts (id, trade_date, arm, outcome) VALUES (?, ?, ?, ?)");
    // 'window_blocked' sorts after 'gate_blocked' in the index but came first in time.
    add.run(1, "2026-10-06", "control", "window_blocked");
    add.run(2, "2026-10-06", "control", "gate_blocked");
    add.run(3, "2026-10-07", "control", "window_blocked");
    add.run(4, null, "control", "filled");
    expect(entryOutcomes(db, "fly_entry_attempts", null)).toEqual([
      { arm: "control", sessions: 2, entered: 0, fills: 0, refusals: { gate_blocked: 1, window_blocked: 1 } },
    ]);
    expect(entryOutcomes(db, "fly_entry_attempts", "2026-10-07")).toEqual([
      { arm: "control", sessions: 1, entered: 0, fills: 0, refusals: { window_blocked: 1 } },
    ]);
  });

  it("is one covering-index scan in group order where flies' (arm, trade_date, outcome) index exists", () => {
    // flies' ledger is ~508k attempt rows; walking them in id order took ~0.9 s per costs request.
    const db = new Database(":memory:");
    db.exec(`CREATE TABLE fly_entry_attempts (id INTEGER PRIMARY KEY, trade_date TEXT, arm TEXT, outcome TEXT, ts TEXT);
             CREATE INDEX idx_fly_attempts_date ON fly_entry_attempts(trade_date, arm);
             CREATE INDEX idx_fly_attempts_arm_date_outcome ON fly_entry_attempts(arm, trade_date, outcome)`);
    const seen: string[] = [];
    const prepare = db.prepare.bind(db);
    db.prepare = ((sql: string) => {
      seen.push(sql);
      return prepare(sql);
    }) as typeof db.prepare;
    entryOutcomes(db, "fly_entry_attempts", null);
    const read = seen.find((s) => s.includes("FROM fly_entry_attempts"))!;
    const plan = prepare(`EXPLAIN QUERY PLAN ${read}`)
      .all()
      .map((r) => String((r as { detail: string }).detail))
      .join(" | ");
    expect(plan).toContain("COVERING INDEX idx_fly_attempts_arm_date_outcome");
    expect(plan).not.toContain("TEMP B-TREE");
  });
});
