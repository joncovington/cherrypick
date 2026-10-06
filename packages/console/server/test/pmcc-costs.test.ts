/**
 * pmcc's costs read over the module's own schema: premium is every short sold (rolls included), the
 * money splits through `positionCash` so each arm's row adds up, the era scopes it like the arm
 * comparison, and entry outcomes count sessions, not ticks. Skips, visibly, where the package is
 * not importable.
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { afterAll, describe, expect, it } from "vitest";

import { loadConfig } from "../src/config.js";
import { readPmccCosts } from "../src/readers/pmcc.js";
import { closePooledDbs } from "../src/readers/db.js";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pmcc-costs-test-"));
const ledger = path.join(dir, "paper_trades.db");
const built =
  spawnSync("python", ["-c", `from cherrypick.pmcc import db; db.connect(r"${ledger}")`], {
    encoding: "utf-8",
    timeout: 60_000,
  }).status === 0;

afterAll(() => closePooledDbs());

describe.skipIf(!built)("pmcc costs over the module's own schema", () => {
  it("judges each arm against every short it sold, in its era", () => {
    const db = new Database(ledger);
    db.exec(`
      INSERT INTO pmcc_positions (position_id, symbol, arm, entry_session, quantity, long_expiration, long_strike,
        short_expiration, short_strike, net_debit, status, closed_session, gross_pnl, fees, entry_slippage,
        exit_slippage, era)
      VALUES ('a', 'SLV', 'shield', '2026-10-06', 1, '2027-10-15', 40, '2026-10-16', 47, 6.0, 'closed', '2026-10-20',
        120.0, 20.0, 5.0, 3.0, 'shield'),
             ('b', 'XSP', 'control', '2026-09-01', 1, '2026-09-26', 600, '2026-09-05', 640, 30.0, 'closed', '2026-09-05',
        50.0, 9.0, 2.0, 1.0, 'redesign');
      INSERT INTO pmcc_legs (position_id, leg_role, occ_symbol, streamer_symbol, expiration, strike, option_type, action, quantity, entry_mid)
      VALUES ('a', 'long_call', 'x', 'x', '2027-10-15', 40, 'C', 'Buy to Open', 1, 8.0),
             ('a', 'short_call_1', 'y', 'y', '2026-10-16', 47, 'C', 'Sell to Open', 1, 1.20),
             ('a', 'short_call_2', 'z', 'z', '2026-10-23', 47, 'C', 'Sell to Open', 1, 0.80);
    `);
    const attempt = db.prepare(
      "INSERT INTO pmcc_entry_attempts (ts, trade_date, symbol, arm, outcome) VALUES ('t', ?, 'SLV', 'shield', ?)",
    );
    for (let i = 0; i < 20; i++) attempt.run("2026-10-06", "entry_pacing");
    attempt.run("2026-10-06", "filled");
    attempt.run("2026-10-07", "ex_dividend_span");
    db.close();

    const config = loadConfig();
    config.paths.pmccDir = dir;
    const shield = readPmccCosts(config, "shield");
    expect(shield.arms.map((a) => a.arm)).toEqual(["shield"]); // control's redesign-era row is out of scope
    const a = shield.arms[0]!;
    expect(a.premium).toBe(200); // (1.20 + 0.80) x 100: both shorts, the roll included
    expect(a.slippage).toBe(8);
    expect(a.fees).toBe(12); // the ledger's 20 total, less slippage
    expect(a.grossPnl - a.fees - a.settlementFees - a.slippage).toBeCloseTo(a.netPnl, 2);
    expect(shield.since).toBe("2026-10-06");
    expect(shield.entryOutcomes).toEqual([
      { arm: "shield", sessions: 2, entered: 1, fills: 1, refusals: { ex_dividend_span: 1 } },
    ]);
    expect(readPmccCosts(config, "ALL").arms.map((x) => x.arm).sort()).toEqual(["control", "shield"]);
  });
});
