/**
 * curve's costs read: entry outcomes are counted per SESSION (a gate refusing every tick is one
 * session), and an arm's premium and split costs come through the money layout. Built through
 * curve's own schema; skips, visibly, where the package is not importable.
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { afterAll, describe, expect, it } from "vitest";

import { loadConfig } from "../src/config.js";
import { readCurve } from "../src/readers/curve.js";
import { closePooledDbs } from "../src/readers/db.js";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "curve-costs-test-"));
const ledger = path.join(dir, "paper_trades.db");
const built =
  spawnSync("python", ["-c", `from cherrypick.curve import db; db.connect(r"${ledger}")`], {
    encoding: "utf-8",
    timeout: 60_000,
  }).status === 0;

afterAll(() => closePooledDbs());

describe.skipIf(!built)("curve costs over the module's own schema", () => {
  it("counts sessions, not ticks, and splits the premium's costs", () => {
    const db = new Database(ledger);
    const attempt = db.prepare(
      "INSERT INTO curve_entry_attempts (ts, trade_date, symbol, arm, outcome) VALUES ('t', ?, 'VXX', ?, ?)",
    );
    for (let i = 0; i < 30; i++) attempt.run("2026-10-07", "control", "spread_too_wide"); // one session
    attempt.run("2026-10-07", "control", "net_credit_below_floor"); // its last refusal
    attempt.run("2026-10-08", "control", "net_credit_below_floor");
    attempt.run("2026-10-08", "control", "filled"); // entered
    attempt.run("2026-10-05", "control", "credit_below_floor"); // before the boundary
    db.exec(`
      INSERT INTO measurement_breaks (break_date, key) VALUES ('2026-10-06', 'defaults.spread_width');
      INSERT INTO curve_positions (position_id, symbol, arm, entry_session, quantity, expiration, short_strike,
        long_strike, entry_credit, status, closed_session, gross_pnl, fees, entry_slippage, exit_slippage, settlement_fees)
      VALUES ('p1', 'VXX', 'control', '2026-10-08', 2, '2026-11-20', 20, 22, 0.30, 'closed', '2026-10-20', 30.0, 14.0, 4.0, 3.0, 0);
    `);
    db.close();

    const config = loadConfig();
    config.paths.curveDir = dir;
    const out = readCurve(config);

    expect(out.outcomesSince).toBe("2026-10-06");
    const since = out.entryOutcomes.find((o) => o.arm === "control")!;
    expect(since).toEqual({ arm: "control", sessions: 2, entered: 1, refusals: { net_credit_below_floor: 1 } });
    expect(out.entryOutcomesAll.find((o) => o.arm === "control")!.sessions).toBe(3);

    const arm = out.arms.find((a) => a.arm === "control")!;
    expect(arm.premium).toBe(60); // 0.30 x 100 x 2
    expect(arm.slippage).toBe(7);
    expect(arm.fees).toBe(7); // the ledger's 14 total, less slippage
    expect(arm.grossPnl - arm.fees - arm.settlementFees - arm.slippage).toBeCloseTo(arm.netPnl, 2);
  });
});
